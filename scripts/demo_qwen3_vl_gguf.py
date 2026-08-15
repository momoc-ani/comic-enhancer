#!/usr/bin/env python3
"""使用独立 llama.cpp ROCm 服务运行 Qwen3-VL GGUF 演示。"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

import httpx
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "qwen3-vl-demo"
DEFAULT_MODEL_PATH = Path(
    r"E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF"
    r"\Qwen3VL-4B-Instruct-Q8_0.gguf"
)
DEFAULT_MMPROJ_PATH = Path(
    r"E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF"
    r"\mmproj-Qwen3VL-4B-Instruct-F16.gguf"
)
DEFAULT_PROMPT = (
    "任务仅限角色身份匹配与定位，不要总结场景、剧情或文字。逐个比较候选角色参考图与"
    "Picture 1 中每个可见人物，优先使用发型轮廓、脸部特征、配饰、服装结构和其他稳定特征；"
    "灰度漫画页中不要仅凭颜色匹配。只返回 JSON 对象，characters 必须为每个候选角色各返回"
    "一项，字段为 name、reference_slot、visible、instances、decision_reason。每个 instance 包含"
    "panel_id、box_2d、match_evidence、counter_evidence、confidence；box_2d 使用 Picture 1 的"
    "0 到 1000 归一化坐标 [x1,y1,x2,y2]。证据不足时 visible=false、instances=[]，不得编造"
    "候选列表外的角色名，不得因为必须匹配而强行给出身份。match_evidence 和 counter_evidence"
    "必须是短字符串数组。另返回 unmatched_people 数组，其中每项只能包含 panel_id、box_2d 和"
    "reason，禁止为未匹配人物输出或猜测姓名。所有 panel_id 必须从 1 开始，按页面从上到下、"
    "从左到右编号；本页最上方分格必须是 panel_id=1，禁止使用 0。"
)


@dataclass(frozen=True)
class ModelFileSpec:
    """描述需要校验的模型文件。"""

    path: Path
    size: int
    sha256: str


MODEL_SPEC = ModelFileSpec(
    path=DEFAULT_MODEL_PATH,
    size=4_280_406_144,
    sha256="054721f478bc5fa6beffb7f38eae575d45298f88cbb8d2f83ef675a727863eb1",
)
MMPROJ_SPEC = ModelFileSpec(
    path=DEFAULT_MMPROJ_PATH,
    size=836_180_256,
    sha256="256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331",
)


# 方法说明：解析演示程序的命令行参数。
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="当前漫画页路径")
    parser.add_argument(
        "--reference",
        type=Path,
        action="append",
        default=[],
        help="角色参考图路径，可重复传入，最多三张",
    )
    parser.add_argument(
        "--reference-name",
        action="append",
        default=[],
        help="参考图对应的准确角色名，顺序必须与 --reference 一致",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="发送给视觉模型的提示词")
    parser.add_argument("--json", action="store_true", help="要求并校验 JSON 输出")
    parser.add_argument("--output", type=Path, help="将最终结果写入指定文件")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--mmproj", type=Path, default=DEFAULT_MMPROJ_PATH)
    parser.add_argument("--server", type=Path, help="显式指定 llama-server.exe")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 表示自动选择空闲端口")
    parser.add_argument("--ctx-size", type=int, default=8192)
    parser.add_argument("--gpu-layers", type=int, default=99)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--image-min-tokens", type=int, default=1024)
    parser.add_argument("--startup-timeout", type=float, default=180)
    parser.add_argument("--request-timeout", type=float, default=300)
    parser.add_argument(
        "--verify-model-hash",
        action="store_true",
        help="除大小外再计算两个模型文件的完整 SHA-256",
    )
    return parser.parse_args(argv)


# 方法说明：以流式读取方式计算大文件的 SHA-256。
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        while chunk := input_file.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# 方法说明：校验模型文件是否存在、大小正确，并按需检查摘要。
def verify_model_file(spec: ModelFileSpec, verify_hash: bool) -> None:
    if not spec.path.is_file():
        raise FileNotFoundError(f"缺少模型文件：{spec.path}")
    actual_size = spec.path.stat().st_size
    if actual_size != spec.size:
        raise RuntimeError(
            f"模型文件大小错误：{spec.path}，实际 {actual_size}，应为 {spec.size}"
        )
    if verify_hash:
        actual_hash = sha256_file(spec.path)
        if actual_hash.lower() != spec.sha256.lower():
            raise RuntimeError(f"模型文件 SHA-256 错误：{spec.path}")


# 方法说明：将本地图片规范化为 PNG 数据地址，兼容不直接解码 WebP 的服务构建。
def image_data_url(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"缺少图片文件：{path}")
    try:
        with Image.open(path) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            output = BytesIO()
            normalized.save(output, format="PNG", optimize=True)
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取图片：{path}") from error
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# 方法说明：构造带明确图片编号和语义的多模态消息内容。
def build_content_parts(
    image: Path,
    references: list[Path],
    prompt: str,
    reference_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    if len(references) > 3:
        raise ValueError("角色参考图最多允许三张")
    names = resolve_reference_names(references, reference_names or [])

    parts: list[dict[str, Any]] = [
        {"type": "text", "text": "Picture 1 = current manga page."},
        {
            "type": "image_url",
            "image_url": {"url": image_data_url(image)},
        },
    ]
    for index, (reference, name) in enumerate(zip(references, names), start=2):
        parts.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f'Picture {index} = candidate character reference slot '
                        f'{index - 1}, exact name = "{name}".'
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url(reference)},
                },
            ]
        )
    mapping = [
        {"name": name, "reference_slot": index}
        for index, name in enumerate(names, start=1)
    ]
    parts.append(
        {
            "type": "text",
            "text": (
                "Allowed candidate mapping: "
                f"{json.dumps(mapping, ensure_ascii=False)}\n{prompt}"
            ),
        }
    )
    return parts


# 方法说明：解析并校验参考图对应的准确角色名称。
def resolve_reference_names(
    references: list[Path],
    explicit_names: list[str],
) -> list[str]:
    if explicit_names and len(explicit_names) != len(references):
        raise ValueError("--reference-name 数量必须与 --reference 一致")
    names = explicit_names or [reference.stem for reference in references]
    normalized = [name.strip() for name in names]
    if any(not name for name in normalized):
        raise ValueError("角色名称不能为空")
    if len(set(normalized)) != len(normalized):
        raise ValueError("角色名称不能重复")
    return normalized


# 方法说明：构造 llama-server 的 OpenAI 兼容聊天请求。
def build_request_payload(
    content: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    json_output: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "qwen3-vl-4b-instruct",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "seed": 42,
        "stream": False,
    }
    if json_output:
        payload["response_format"] = {"type": "json_object"}
    return payload


# 方法说明：选择仅绑定本机回环地址的空闲 TCP 端口。
def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


# 方法说明：查找安装脚本解压出的 llama-server 可执行文件。
def find_server_executable(explicit: Path | None, runtime_root: Path) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"缺少 llama-server：{explicit}")
        return explicit.resolve()

    candidates = sorted((runtime_root / "llamacpp-rocm").rglob("llama-server.exe"))
    if not candidates:
        raise FileNotFoundError(
            "未找到 llama-server.exe，请先运行 scripts/setup_qwen3_vl_demo.ps1"
        )
    return candidates[-1].resolve()


# 方法说明：生成限制为本机访问的 llama-server 启动参数。
def build_server_command(
    executable: Path,
    model: Path,
    mmproj: Path,
    host: str,
    port: int,
    context_size: int,
    gpu_layers: int,
    parallel: int = 1,
    image_min_tokens: int = 1024,
) -> list[str]:
    return [
        str(executable),
        "-m",
        str(model),
        "--mmproj",
        str(mmproj),
        "-ngl",
        str(gpu_layers),
        "-c",
        str(context_size),
        "--parallel",
        str(parallel),
        "--image-min-tokens",
        str(image_min_tokens),
        "--host",
        host,
        "--port",
        str(port),
        "--jinja",
    ]


# 方法说明：读取启动日志末尾，便于报告模型加载错误。
def log_tail(path: Path, limit: int = 6000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


# 方法说明：等待 llama-server 完成模型加载并通过健康检查。
def wait_for_server(
    process: subprocess.Popen[bytes],
    base_url: str,
    timeout_seconds: float,
    log_path: Path,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(timeout=2) as client:
        while time.monotonic() < deadline:
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"llama-server 提前退出，退出码 {return_code}\n{log_tail(log_path)}"
                )
            try:
                response = client.get(f"{base_url}/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise TimeoutError(f"llama-server 启动超时\n{log_tail(log_path)}")


# 方法说明：启动本次演示专用的 llama-server 进程。
def start_server(
    executable: Path,
    model: Path,
    mmproj: Path,
    runtime_root: Path,
    host: str,
    port: int,
    context_size: int,
    gpu_layers: int,
    startup_timeout: float,
    image_min_tokens: int,
) -> tuple[subprocess.Popen[bytes], Any, Path, str]:
    selected_port = port or find_free_port(host)
    base_url = f"http://{host}:{selected_port}"
    log_directory = runtime_root / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"llama-server-{selected_port}.log"
    log_handle = log_path.open("wb")
    command = build_server_command(
        executable,
        model,
        mmproj,
        host,
        selected_port,
        context_size,
        gpu_layers,
        image_min_tokens=image_min_tokens,
    )
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )
    process = subprocess.Popen(
        command,
        cwd=executable.parent,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creation_flags,
    )
    try:
        wait_for_server(process, base_url, startup_timeout, log_path)
    except BaseException:
        stop_server(process, log_handle)
        raise
    return process, log_handle, log_path, base_url


# 方法说明：终止本次演示启动的服务进程并释放日志文件。
def stop_server(process: subprocess.Popen[bytes], log_handle: Any) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    finally:
        log_handle.close()


# 方法说明：向 llama-server 发送多模态聊天请求。
def request_completion(
    base_url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_seconds, connect=10)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url}/v1/chat/completions", json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = response.text[:2000]
            raise RuntimeError(
                f"llama-server 返回 HTTP {response.status_code}：{detail}"
            ) from error
        result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("llama-server 返回了非对象 JSON")
    return result


# 方法说明：从 OpenAI 兼容响应中提取最终文本。
def extract_response_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("llama-server 响应缺少 choices[0].message.content") from error
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("llama-server 返回了空内容")
    return content.strip()


# 方法说明：格式化模型输出，并按需验证或写入 JSON。
def format_output(
    content: str,
    json_output: bool,
    allowed_character_names: list[str] | None = None,
) -> str:
    if not json_output:
        return content
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"模型没有返回有效 JSON：{error}") from error
    if allowed_character_names:
        validate_character_analysis(value, allowed_character_names)
    return json.dumps(value, ensure_ascii=False, indent=2)


# 方法说明：拒绝缺失候选角色或包含模型自创角色名的分析结果。
def validate_character_analysis(value: Any, allowed_names: list[str]) -> None:
    if not isinstance(value, dict):
        raise RuntimeError("角色分析结果必须是 JSON 对象")
    characters = value.get("characters")
    if not isinstance(characters, list):
        raise RuntimeError("角色分析结果缺少 characters 数组")

    returned_names: list[str] = []
    for character in characters:
        if not isinstance(character, dict):
            raise RuntimeError("characters 中的项目必须是 JSON 对象")
        name = character.get("name")
        if name not in allowed_names:
            raise RuntimeError(f"模型返回了候选列表外的角色名：{name}")
        if character.get("reference_slot") != allowed_names.index(name) + 1:
            raise RuntimeError(f"角色 {name} 的 reference_slot 错误")
        if not isinstance(character.get("visible"), bool):
            raise RuntimeError(f"角色 {name} 的 visible 必须是布尔值")
        instances = character.get("instances")
        if not isinstance(instances, list):
            raise RuntimeError(f"角色 {name} 的 instances 必须是数组")
        if character["visible"] != bool(instances):
            raise RuntimeError(f"角色 {name} 的 visible 与 instances 不一致")
        for instance in instances:
            validate_character_instance(instance, name)
        returned_names.append(name)

    if len(returned_names) != len(set(returned_names)):
        raise RuntimeError("角色分析结果包含重复候选角色")
    missing_names = [name for name in allowed_names if name not in returned_names]
    if missing_names:
        raise RuntimeError(f"角色分析结果缺少候选角色：{', '.join(missing_names)}")

    unmatched_people = value.get("unmatched_people")
    if not isinstance(unmatched_people, list):
        raise RuntimeError("角色分析结果缺少 unmatched_people 数组")
    forbidden_fields = {"name", "reference_slot", "visible", "instances"}
    for person in unmatched_people:
        if not isinstance(person, dict):
            raise RuntimeError("unmatched_people 中的项目必须是 JSON 对象")
        if forbidden_fields.intersection(person):
            raise RuntimeError("未匹配人物不得包含姓名或候选角色字段")
        if set(person) != {"panel_id", "box_2d", "reason"}:
            raise RuntimeError("未匹配人物只能包含 panel_id、box_2d 和 reason")
        validate_box(person.get("box_2d"), "未匹配人物")
        if not isinstance(person.get("panel_id"), int) or person["panel_id"] < 1:
            raise RuntimeError("未匹配人物的 panel_id 无效")
        if not isinstance(person.get("reason"), str) or not person["reason"].strip():
            raise RuntimeError("未匹配人物的 reason 无效")


# 方法说明：校验角色实例的分格、坐标、证据和置信度字段。
def validate_character_instance(instance: Any, character_name: str) -> None:
    if not isinstance(instance, dict):
        raise RuntimeError(f"角色 {character_name} 的实例必须是 JSON 对象")
    if not isinstance(instance.get("panel_id"), int) or instance["panel_id"] < 1:
        raise RuntimeError(f"角色 {character_name} 的 panel_id 无效")
    validate_box(instance.get("box_2d"), f"角色 {character_name}")
    for field in ("match_evidence", "counter_evidence"):
        evidence = instance.get(field)
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise RuntimeError(f"角色 {character_name} 的 {field} 必须是字符串数组")
    confidence = instance.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise RuntimeError(f"角色 {character_name} 的 confidence 无效")


# 方法说明：校验归一化角色框位于有效坐标范围且方向正确。
def validate_box(box: Any, label: str) -> None:
    if (
        not isinstance(box, list)
        or len(box) != 4
        or not all(isinstance(value, (int, float)) for value in box)
    ):
        raise RuntimeError(f"{label} 的 box_2d 必须包含四个数值")
    x1, y1, x2, y2 = box
    if not all(0 <= value <= 1000 for value in box) or x1 >= x2 or y1 >= y2:
        raise RuntimeError(f"{label} 的 box_2d 坐标无效")


# 方法说明：执行模型校验、服务启动、请求和清理的完整演示流程。
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if len(args.reference) > 3:
        raise ValueError("角色参考图最多允许三张")

    model_spec = ModelFileSpec(args.model, MODEL_SPEC.size, MODEL_SPEC.sha256)
    mmproj_spec = ModelFileSpec(args.mmproj, MMPROJ_SPEC.size, MMPROJ_SPEC.sha256)
    verify_model_file(model_spec, args.verify_model_hash)
    verify_model_file(mmproj_spec, args.verify_model_hash)

    executable = find_server_executable(args.server, args.runtime_root)
    reference_names = resolve_reference_names(args.reference, args.reference_name)
    content = build_content_parts(
        args.image,
        args.reference,
        args.prompt,
        reference_names,
    )
    payload = build_request_payload(
        content,
        args.max_tokens,
        args.temperature,
        args.json,
    )

    process, log_handle, log_path, base_url = start_server(
        executable=executable,
        model=args.model,
        mmproj=args.mmproj,
        runtime_root=args.runtime_root,
        host=args.host,
        port=args.port,
        context_size=args.ctx_size,
        gpu_layers=args.gpu_layers,
        startup_timeout=args.startup_timeout,
        image_min_tokens=args.image_min_tokens,
    )
    try:
        response = request_completion(base_url, payload, args.request_timeout)
        output = format_output(
            extract_response_content(response),
            args.json,
            reference_names if args.reference else None,
        )
    except BaseException as error:
        raise RuntimeError(f"视觉模型请求失败：{error}\n{log_tail(log_path)}") from error
    finally:
        stop_server(process, log_handle)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    print(f"\nllama-server 日志：{log_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
