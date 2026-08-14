#!/usr/bin/env python3
"""Compare standalone Cobra and FLUX.2 Klein candidates on one manifest."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import statistics
import sys
import time
import uuid

import httpx
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "service"))

from comic_enhancer.backends import ComfyUIBackend

from benchmark_api import (
    RemoteResourceMonitor,
    image_metrics,
    percentile,
    summarize_resources,
)
from benchmark_comfyui import upload, wait_for_output


def manifest_pages(path: Path) -> tuple[dict, list[Path]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = [(PROJECT_ROOT / item).resolve() for item in data.get("test_pages", [])]
    missing = [str(page) for page in pages if not page.is_file()]
    if not pages or missing:
        raise ValueError(f"测试页清单无效，缺失文件: {missing}")
    return data, pages


def load_rgb(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as source:
        return ImageOps.exif_transpose(source).convert("RGB").copy()


def run_cobra(
    client: httpx.Client,
    page: Path,
    references: list[Path],
    *,
    seed: int,
    steps: int,
    top_k: int,
) -> tuple[bytes, int | None, str | None]:
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("image", (page.name, page.read_bytes(), "application/octet-stream"))
    ]
    files.extend(
        (
            "references",
            (reference.name, reference.read_bytes(), "application/octet-stream"),
        )
        for reference in references
    )
    response = client.post(
        "/v1/colorize",
        files=files,
        data={
            "style": "line + shadow",
            "seed": str(seed),
            "steps": str(steps),
            "top_k": str(top_k),
        },
    )
    response.raise_for_status()
    backend_ms = response.headers.get("X-Inference-Ms")
    return (
        response.content,
        int(backend_ms) if backend_ms and backend_ms.isdigit() else None,
        None,
    )


def run_flux2(
    client: httpx.Client,
    page: Path,
    reference_inputs: dict[str, str],
    workflow: dict,
    *,
    run_index: int,
    timeout_seconds: int,
) -> tuple[bytes, int | None, str]:
    prompt = deepcopy(workflow)
    for node in prompt.values():
        node_inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if isinstance(node_inputs.get("noise_seed"), int):
            node_inputs["noise_seed"] += run_index
    inputs = {
        "INPUT_IMAGE": upload(client, page, "INPUT_IMAGE"),
        **reference_inputs,
    }
    output_nodes = ComfyUIBackend._bind_io(
        prompt,
        input_images=inputs,
        output_prefix=f"comic-enhancer/candidate-{uuid.uuid4().hex}",
    )
    response = client.post(
        "/prompt",
        json={"prompt": prompt, "client_id": uuid.uuid4().hex},
    )
    response.raise_for_status()
    prompt_id = response.json()["prompt_id"]
    image_info, _ = wait_for_output(
        client,
        prompt_id,
        output_nodes,
        timeout_seconds,
    )
    result = client.get("/view", params=image_info)
    result.raise_for_status()
    return result.content, None, prompt_id


def summarize(results: list[dict[str, object]]) -> dict[str, object]:
    elapsed = [int(item["client_elapsed_ms"]) for item in results]
    backend = [
        int(item["backend_elapsed_ms"])
        for item in results
        if item.get("backend_elapsed_ms") is not None
    ]
    protected_metrics = [item["protected_metrics"] for item in results]
    raw_metrics = [item["raw_metrics"] for item in results]
    return {
        "pages": len(results),
        "client_p50_ms": round(statistics.median(elapsed)) if elapsed else 0,
        "client_p95_ms": percentile(elapsed, 95),
        "backend_p50_ms": round(statistics.median(backend)) if backend else None,
        "backend_p95_ms": percentile(backend, 95) if backend else None,
        "raw_median_saturated_pixel_ratio": round(
            statistics.median(float(item["saturated_pixel_ratio"]) for item in raw_metrics),
            4,
        ),
        "raw_median_active_hue_bins": round(
            statistics.median(int(item["active_hue_bins"]) for item in raw_metrics),
            2,
        ),
        "raw_maximum_dominant_hue_ratio": max(
            float(item["dominant_hue_ratio"]) for item in raw_metrics
        ),
        "protected_median_saturated_pixel_ratio": round(
            statistics.median(
                float(item["saturated_pixel_ratio"]) for item in protected_metrics
            ),
            4,
        ),
        "protected_median_active_hue_bins": round(
            statistics.median(int(item["active_hue_bins"]) for item in protected_metrics),
            2,
        ),
        "protected_maximum_dominant_hue_ratio": max(
            float(item["dominant_hue_ratio"]) for item in protected_metrics
        ),
        "minimum_dark_pixel_retention": min(
            float(item["dark_pixel_retention"]) for item in protected_metrics
        ),
        "minimum_mid_dark_pixel_retention": min(
            float(item["mid_dark_pixel_retention"]) for item in protected_metrics
        ),
        "minimum_white_pixel_retention": min(
            float(item["white_pixel_retention"]) for item in protected_metrics
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=("cobra", "flux2-klein-4b"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", required=True)
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--phase", choices=("cold", "warm"), required=True)
    parser.add_argument("--cobra-steps", type=int, default=10)
    parser.add_argument("--cobra-top-k", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--resource-ssh-host")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "benchmarks" / "candidates",
    )
    args = parser.parse_args()

    manifest, pages = manifest_pages(args.manifest.resolve())
    references = [item.resolve() for item in args.reference]
    missing_references = [str(item) for item in references if not item.is_file()]
    if missing_references:
        parser.error(f"参考图不存在: {missing_references}")
    if args.candidate == "flux2-klein-4b" and len(references) != 3:
        parser.error("FLUX.2 当前候选工作流需要恰好 3 张角色参考图")
    if args.candidate == "flux2-klein-4b" and not args.workflow:
        parser.error("FLUX.2 候选必须提供 --workflow")

    workflow = None
    if args.workflow:
        workflow = json.loads(args.workflow.resolve().read_text(encoding="utf-8"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir.resolve() / f"{args.candidate}-{args.phase}-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    monitor = None
    if args.resource_ssh_host:
        monitor = RemoteResourceMonitor(args.resource_ssh_host, 1.0)
        monitor.start()

    results: list[dict[str, object]] = []
    resource_samples: list[dict[str, object]] = []
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
            reference_inputs = {}
            if args.candidate == "flux2-klein-4b":
                reference_inputs = {
                    f"REFERENCE_IMAGE_{index}": upload(
                        client,
                        reference,
                        f"REFERENCE_IMAGE_{index}",
                    )
                    for index, reference in enumerate(references, 1)
                }
            for index, page in enumerate(pages, 1):
                if monitor:
                    monitor.set_page_index(index)
                started = time.perf_counter()
                if args.candidate == "cobra":
                    raw_bytes, backend_ms, prompt_id = run_cobra(
                        client,
                        page,
                        references,
                        seed=args.seed + index - 1,
                        steps=args.cobra_steps,
                        top_k=args.cobra_top_k,
                    )
                else:
                    raw_bytes, backend_ms, prompt_id = run_flux2(
                        client,
                        page,
                        reference_inputs,
                        workflow,
                        run_index=index - 1,
                        timeout_seconds=args.timeout,
                    )
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                source_bytes = page.read_bytes()
                raw_image = load_rgb(raw_bytes)
                protected_image = ComfyUIBackend._protect_source_structure(
                    source_bytes,
                    raw_image,
                )
                raw_path = output_dir / f"page-{index:02d}-raw.png"
                protected_path = output_dir / f"page-{index:02d}-protected.png"
                raw_image.save(raw_path, format="PNG", optimize=True)
                protected_image.save(protected_path, format="PNG", optimize=True)
                protected_stream = BytesIO()
                protected_image.save(protected_stream, format="PNG")
                item = {
                    "page": str(page.relative_to(PROJECT_ROOT)),
                    "prompt_id": prompt_id,
                    "client_elapsed_ms": elapsed_ms,
                    "backend_elapsed_ms": backend_ms,
                    "raw_output": str(raw_path.relative_to(PROJECT_ROOT)),
                    "protected_output": str(protected_path.relative_to(PROJECT_ROOT)),
                    "raw_metrics": image_metrics(source_bytes, raw_bytes),
                    "protected_metrics": image_metrics(
                        source_bytes,
                        protected_stream.getvalue(),
                    ),
                }
                results.append(item)
                print(f"第 {index} 页: {elapsed_ms} ms -> {protected_path}", flush=True)
    finally:
        if monitor:
            monitor.set_page_index(None)
            resource_samples = monitor.stop()

    report = {
        "candidate": args.candidate,
        "phase": args.phase,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(args.manifest),
        "benchmark_scope": manifest.get("scope"),
        "reference_count": len(references),
        "summary": summarize(results),
        "resource_summary": summarize_resources(resource_samples),
        "results": results,
        "resource_samples": resource_samples,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
