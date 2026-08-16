from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from io import BytesIO
import hashlib
import json
import logging
import re
import threading
import time
import uuid

import httpx
from PIL import Image, ImageOps

from ...logging_utils import log_operation


logger = logging.getLogger(__name__)
WorkflowMutator = Callable[[dict], None]


class ComfyUITransport:
    """封装 ComfyUI 上传、提交、轮询和结果下载。"""

    # 方法说明：初始化 ComfyUI 地址、超时和轮询参数。
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._profile_ready_cache: dict[str, tuple[float, bool]] = {}
        self._input_upload_cache: dict[str, str] = {}
        self._input_upload_cache_lock = threading.Lock()

    # 方法说明：检查 ComfyUI 基础服务是否可以响应。
    def ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/system_stats", timeout=2)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # 方法说明：按档位短暂缓存 ComfyUI 与工作流可用性检查结果。
    def profile_ready(
        self,
        cache_key: str,
        *,
        enabled: bool,
        workflow_supported: bool,
    ) -> bool:
        if not enabled or not workflow_supported:
            return False
        now = time.monotonic()
        cached_until, cached_value = self._profile_ready_cache.get(
            cache_key,
            (0.0, False),
        )
        if now < cached_until:
            return cached_value
        ready = self.ready()
        self._profile_ready_cache[cache_key] = (now + (5 if ready else 1), ready)
        return ready

    # 方法说明：绑定输入后提交完整工作流并下载最后一个结果图。
    def run(
        self,
        workflow_template: dict,
        *,
        input_images: dict[str, bytes],
        output_prefix: str,
        prepare_workflow: WorkflowMutator | None = None,
    ) -> Image.Image:
        started = time.perf_counter()
        workflow = json.loads(json.dumps(workflow_template))
        if prepare_workflow is not None:
            prepare_workflow(workflow)
        workflow_digest = _workflow_digest(workflow)
        final_prompts = _workflow_prompts(workflow)
        log_operation(
            logger,
            logging.INFO,
            feature="ComfyUI工作流准备",
            parameters={
                "base_url": self.base_url,
                "timeout_seconds": self.timeout_seconds,
                "poll_interval_seconds": self.poll_interval_seconds,
                "input_roles": sorted(input_images),
            },
            result={
                "status": "ready",
                "workflow_digest": workflow_digest,
                "node_count": len(workflow),
                "node_types": _workflow_node_types(workflow),
                "titled_nodes": _workflow_titles(workflow),
                "runtime_parameters": _workflow_runtime_parameters(workflow),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        if final_prompts:
            log_operation(
                logger,
                logging.INFO,
                feature="ComfyUI最终提示词",
                parameters={
                    "workflow_digest": workflow_digest,
                    "prompt_count": len(final_prompts),
                },
                result={
                    "status": "ready",
                    "prompts": final_prompts,
                },
                full_text_keys={"text"},
            )
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        ) as client:
            uploaded_by_digest: dict[str, str] = {}
            uploaded_inputs: dict[str, str] = {}
            reused_uploads = 0
            new_uploads = 0
            for role, image_bytes in input_images.items():
                digest = hashlib.sha256(image_bytes).hexdigest()
                uploaded = uploaded_by_digest.get(digest)
                reused = uploaded is not None
                if uploaded is None:
                    with self._input_upload_cache_lock:
                        uploaded = self._input_upload_cache.get(digest)
                    reused = uploaded is not None
                if uploaded is None:
                    uploaded = self._upload(client, image_bytes, role.lower())
                    with self._input_upload_cache_lock:
                        self._input_upload_cache[digest] = uploaded
                    uploaded_by_digest[digest] = uploaded
                    new_uploads += 1
                elif reused:
                    reused_uploads += 1
                uploaded_inputs[role] = uploaded
            log_operation(
                logger,
                logging.INFO,
                feature="ComfyUI输入上传",
                parameters={
                    "workflow_digest": workflow_digest,
                    "input_roles": sorted(input_images),
                },
                result={
                    "status": "success",
                    "unique_uploads": len(uploaded_by_digest),
                    "new_uploads": new_uploads,
                    "reused_uploads": reused_uploads,
                    "inputs": _image_input_summary(input_images),
                },
            )
            output_nodes = bind_io(
                workflow,
                input_images=uploaded_inputs,
                output_prefix=output_prefix,
            )
            submitted_workflow_digest = _workflow_digest(workflow)
            log_operation(
                logger,
                logging.INFO,
                feature="ComfyUI输入输出绑定",
                parameters={
                    "prepared_workflow_digest": workflow_digest,
                    "submitted_workflow_digest": submitted_workflow_digest,
                    "output_prefix": output_prefix,
                },
                result={
                    "status": "success",
                    "output_nodes": list(output_nodes),
                    "bound_inputs": sorted(uploaded_inputs),
                },
            )
            queued = client.post(
                "/prompt",
                json={"prompt": workflow, "client_id": uuid.uuid4().hex},
            )
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            log_operation(
                logger,
                logging.INFO,
                feature="ComfyUI工作流提交",
                parameters={
                    "workflow_digest": submitted_workflow_digest,
                    "output_nodes": list(output_nodes),
                },
                result={"status": "queued", "prompt_id": prompt_id},
            )
            image_info = self._wait_for_output(
                client,
                prompt_id,
                output_nodes,
                workflow_digest=submitted_workflow_digest,
            )
            result = client.get("/view", params=image_info)
            result.raise_for_status()
        with Image.open(BytesIO(result.content)) as generated_file:
            generated = ImageOps.exif_transpose(generated_file).convert("RGB").copy()
        log_operation(
            logger,
            logging.INFO,
            feature="ComfyUI结果下载",
            parameters={
                "workflow_digest": submitted_workflow_digest,
                "prompt_id": prompt_id,
                "image_type": image_info.get("type", "output"),
            },
            result={
                "status": "success",
                "filename": image_info.get("filename", ""),
                "size": list(generated.size),
                "bytes": len(result.content),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return generated

    # 方法说明：将图像字节规范化为 PNG 后上传到 ComfyUI。
    def _upload(
        self,
        client: httpx.Client,
        image_bytes: bytes,
        role: str,
    ) -> str:
        upload_name = f"comic-enhancer-{role}-{uuid.uuid4().hex}.png"
        normalized = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source:
            ImageOps.exif_transpose(source).convert("RGB").save(
                normalized,
                format="PNG",
            )
        upload = client.post(
            "/upload/image",
            files={"image": (upload_name, normalized.getvalue(), "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
        upload.raise_for_status()
        return comfy_path(upload.json())

    # 方法说明：轮询 ComfyUI 历史记录直到获得输出或超时。
    def _wait_for_output(
        self,
        client: httpx.Client,
        prompt_id: str,
        output_nodes: tuple[str, ...],
        *,
        workflow_digest: str = "",
    ) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        poll_count = 0
        last_status = ""
        while time.monotonic() < deadline:
            poll_count += 1
            response = client.get(f"/history/{prompt_id}")
            response.raise_for_status()
            history = response.json().get(prompt_id)
            if history:
                status = history.get("status", {})
                status_name = str(status.get("status_str", "running"))
                if status_name != last_status:
                    log_operation(
                        logger,
                        logging.INFO,
                        feature="ComfyUI任务轮询",
                        parameters={
                            "prompt_id": prompt_id,
                            "workflow_digest": workflow_digest,
                            "poll_count": poll_count,
                        },
                        result={"status": status_name},
                    )
                    last_status = status_name
                if status.get("status_str") == "error":
                    log_operation(
                        logger,
                        logging.ERROR,
                        feature="ComfyUI任务轮询",
                        parameters={
                            "prompt_id": prompt_id,
                            "workflow_digest": workflow_digest,
                        },
                        result={"status": "failed", "stage": "comfyui"},
                    )
                    raise RuntimeError(f"ComfyUI prompt failed: {status}")
                outputs = history.get("outputs", {})
                for node_id in reversed(output_nodes):
                    images = outputs.get(node_id, {}).get("images", [])
                    if images:
                        image = images[-1]
                        log_operation(
                            logger,
                            logging.INFO,
                            feature="ComfyUI任务轮询",
                            parameters={
                                "prompt_id": prompt_id,
                                "workflow_digest": workflow_digest,
                                "poll_count": poll_count,
                                "output_node": node_id,
                            },
                            result={
                                "status": "output_ready",
                                "filename": image.get("filename", ""),
                            },
                        )
                        return {
                            "filename": image["filename"],
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                }
            time.sleep(self.poll_interval_seconds)
        log_operation(
            logger,
            logging.ERROR,
            feature="ComfyUI任务轮询",
            parameters={
                "prompt_id": prompt_id,
                "workflow_digest": workflow_digest,
                "poll_count": poll_count,
                "timeout_seconds": self.timeout_seconds,
            },
            result={"status": "timeout"},
        )
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")


# 方法说明：发现并绑定工作流的输入、参考图和输出节点。
def bind_io(
    workflow: dict,
    *,
    input_images: dict[str, str],
    output_prefix: str,
) -> tuple[str, ...]:
    load_nodes = [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "LoadImage"
    ]
    if len(load_nodes) == 1 and "INPUT_IMAGE" in input_images:
        load_nodes[0][1].setdefault("inputs", {})["image"] = input_images[
            "INPUT_IMAGE"
        ]
    elif len(load_nodes) > 1:
        discovered_roles: set[str] = set()
        for _, node in load_nodes:
            role = str(node.get("_meta", {}).get("title", "")).strip().upper()
            if role in input_images:
                node.setdefault("inputs", {})["image"] = input_images[role]
                discovered_roles.add(role)
        missing = sorted(set(input_images) - discovered_roles)
        if missing:
            raise RuntimeError(
                "ComfyUI workflow is missing titled LoadImage nodes: "
                + ", ".join(missing)
            )
    else:
        raise RuntimeError(
            "ComfyUI workflow must contain exactly one LoadImage node or "
            "titled LoadImage nodes for all inputs; "
            f"found {len(load_nodes)}"
        )

    output_nodes = tuple(
        str(node_id)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "SaveImage"
    )
    if not output_nodes:
        raise RuntimeError("ComfyUI workflow must contain at least one SaveImage node")
    for node_id in output_nodes:
        workflow[node_id].setdefault("inputs", {})["filename_prefix"] = output_prefix

    serialized = json.dumps(workflow, ensure_ascii=False)
    placeholders = sorted(set(re.findall(r"\$\{[^}]+\}", serialized)))
    if placeholders:
        raise RuntimeError(
            "ComfyUI workflow contains runtime placeholders: "
            + ", ".join(placeholders)
        )
    return output_nodes


# 方法说明：拼接 ComfyUI 上传文件的内部路径。
def comfy_path(uploaded: dict) -> str:
    name = uploaded["name"]
    subfolder = uploaded.get("subfolder", "")
    return f"{subfolder}/{name}" if subfolder else name


# 方法说明：生成已提交 ComfyUI 工作流的短摘要，避免记录完整提示词和图片内容。
def _workflow_digest(workflow: dict) -> str:
    serialized = json.dumps(workflow, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


# 方法说明：统计工作流节点类型，帮助定位实际执行的节点结构。
def _workflow_node_types(workflow: dict) -> dict[str, int]:
    counts = Counter(
        str(node.get("class_type", "unknown"))
        for node in workflow.values()
        if isinstance(node, dict)
    )
    return dict(sorted(counts.items()))


# 方法说明：提取工作流节点标题摘要，避免把节点参数全文写入日志。
def _workflow_titles(workflow: dict) -> list[str]:
    titles = {
        str(node.get("_meta", {}).get("title", "")).strip()
        for node in workflow.values()
        if isinstance(node, dict)
    }
    return sorted(title for title in titles if title)[:32]


# 方法说明：提取动态绑定完成后的全部 CLIP 文本节点及完整提示词。
def _workflow_prompts(workflow: dict) -> list[dict[str, str]]:
    prompts: list[dict[str, str]] = []
    for node_id, node in sorted(workflow.items(), key=lambda item: str(item[0])):
        if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
            continue
        text = node.get("inputs", {}).get("text")
        if not isinstance(text, str):
            continue
        prompts.append(
            {
                "node_id": str(node_id),
                "title": str(node.get("_meta", {}).get("title", "")),
                "text": text,
            }
        )
    return prompts


# 方法说明：提取工作流中实际提交的模型、采样和尺寸等标量参数。
def _workflow_runtime_parameters(workflow: dict) -> list[dict[str, object]]:
    excluded_inputs = {
        "clip",
        "conditioning",
        "filename_prefix",
        "guider",
        "image",
        "images",
        "latent",
        "latent_image",
        "model",
        "noise",
        "pixels",
        "prompt",
        "sampler",
        "samples",
        "sigmas",
        "text",
        "vae",
    }
    parameters: list[dict[str, object]] = []
    for node_id, node in sorted(workflow.items(), key=lambda item: str(item[0])):
        if not isinstance(node, dict):
            continue
        scalar_inputs = {
            str(key): value
            for key, value in node.get("inputs", {}).items()
            if key not in excluded_inputs
            and isinstance(value, (str, int, float, bool))
        }
        if not scalar_inputs:
            continue
        parameters.append(
            {
                "node_id": str(node_id),
                "class_type": str(node.get("class_type", "unknown")),
                "title": str(node.get("_meta", {}).get("title", "")),
                "inputs": scalar_inputs,
            }
        )
    return parameters


# 方法说明：记录输入图片的角色、尺寸、字节数和短哈希。
def _image_input_summary(input_images: dict[str, bytes]) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for role, image_bytes in input_images.items():
        size: list[int] | None = None
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                size = list(ImageOps.exif_transpose(image).size)
        except Exception:
            size = None
        summary[role] = {
            "size": size,
            "bytes": len(image_bytes),
            "digest": hashlib.sha256(image_bytes).hexdigest()[:12],
        }
    return summary
