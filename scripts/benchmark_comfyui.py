#!/usr/bin/env python3
"""Run a self-contained API workflow and save timing evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))

from comic_enhancer.inference.comfyui.transport import bind_io, comfy_path


# 方法说明：上传文件并返回服务端文件名。
def upload(client: httpx.Client, path: Path, role: str) -> str:
    with path.open("rb") as stream:
        response = client.post(
            "/upload/image",
            files={"image": (f"benchmark-{role.lower()}-{uuid.uuid4().hex}{path.suffix}", stream)},
            data={"type": "input", "overwrite": "true"},
        )
    response.raise_for_status()
    return comfy_path(response.json())


# 方法说明：轮询 ComfyUI 任务直到获得输出。
def wait_for_output(
    client: httpx.Client,
    prompt_id: str,
    output_nodes: tuple[str, ...],
    timeout_seconds: int,
) -> tuple[dict[str, str], dict]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/history/{prompt_id}")
        response.raise_for_status()
        history = response.json().get(prompt_id)
        if history:
            status = history.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status, ensure_ascii=False))
            outputs = history.get("outputs", {})
            for node_id in reversed(output_nodes):
                images = outputs.get(node_id, {}).get("images", [])
                if images:
                    return images[-1], history
        time.sleep(0.25)
    raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")


# 方法说明：解析命令行参数并执行程序主流程。
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        metavar="ROLE=PATH",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/benchmarks"))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--seed-step",
        type=int,
        default=0,
        help="Increment integer seed inputs per run to bypass ComfyUI node cache",
    )
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    inputs: dict[str, Path] = {}
    for item in args.input:
        role, separator, value = item.partition("=")
        if not separator or not role or not value:
            parser.error(f"invalid --input value: {item}")
        path = Path(value).resolve()
        if not path.is_file():
            parser.error(f"input not found: {path}")
        inputs[role.strip().upper()] = path

    workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = []
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout,
    ) as client:
        uploaded = {
            role: upload(client, path, role)
            for role, path in inputs.items()
        }
        for run in range(1, args.repeat + 1):
            prompt = deepcopy(workflow)
            if args.seed_step:
                for node in prompt.values():
                    node_inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
                    seed = node_inputs.get("seed")
                    if isinstance(seed, int):
                        node_inputs["seed"] = seed + (run - 1) * args.seed_step
            prefix = f"comic-enhancer/benchmark-{uuid.uuid4().hex}"
            output_nodes = bind_io(
                prompt,
                input_images=uploaded,
                output_prefix=prefix,
            )
            started = time.perf_counter()
            response = client.post(
                "/prompt",
                json={"prompt": prompt, "client_id": uuid.uuid4().hex},
            )
            response.raise_for_status()
            prompt_id = response.json()["prompt_id"]
            image_info, history = wait_for_output(
                client,
                prompt_id,
                output_nodes,
                args.timeout,
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            image = client.get("/view", params=image_info)
            image.raise_for_status()
            extension = Path(image_info["filename"]).suffix or ".png"
            output = args.output_dir / f"run-{run:02d}-{prompt_id}{extension}"
            output.write_bytes(image.content)
            report.append(
                {
                    "run": run,
                    "prompt_id": prompt_id,
                    "elapsed_ms": elapsed_ms,
                    "output": str(output),
                    "status_messages": history.get("status", {}).get("messages", []),
                }
            )
            print(f"第 {run} 轮: {elapsed_ms} ms -> {output}", flush=True)

    report_path = args.output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
