#!/usr/bin/env python3
"""Compare Cobra and FLUX.2 Klein ComfyUI workflows on one manifest."""

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
    reference_inputs: dict[str, str],
    workflow: dict,
    *,
    seed: int,
    steps: int,
    top_k: int,
    style: str,
    timeout_seconds: int,
) -> tuple[bytes, int | None, str]:
    prompt = deepcopy(workflow)
    cobra_nodes = [
        node
        for node in prompt.values()
        if isinstance(node, dict) and node.get("class_type") == "CobraColorize"
    ]
    if len(cobra_nodes) != 1:
        raise ValueError("Cobra workflow must contain exactly one CobraColorize node")
    cobra_nodes[0]["inputs"].update(
        {
            "reference_count": len(reference_inputs),
            "seed": seed,
            "steps": steps,
            "top_k": top_k,
            "style": style,
        }
    )
    uploaded_references = list(reference_inputs.values())
    inputs = {
        "INPUT_IMAGE": upload(client, page, "INPUT_IMAGE"),
        **{
            f"REFERENCE_IMAGE_{index}": uploaded_references[
                min(index - 1, len(uploaded_references) - 1)
            ]
            for index in range(1, 13)
        },
    }
    output_nodes = ComfyUIBackend._bind_io(
        prompt,
        input_images=inputs,
        output_prefix=f"comic-enhancer/candidate-cobra-{uuid.uuid4().hex}",
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
    geometry_metrics = [item["geometry_metrics"] for item in results]
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
        "geometry_median_saturated_pixel_ratio": round(
            statistics.median(
                float(item["saturated_pixel_ratio"]) for item in geometry_metrics
            ),
            4,
        ),
        "geometry_median_active_hue_bins": round(
            statistics.median(int(item["active_hue_bins"]) for item in geometry_metrics),
            2,
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
    parser.add_argument(
        "--cobra-style",
        choices=("line + shadow", "line"),
        default="line + shadow",
    )
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
    if args.candidate == "cobra" and not 1 <= len(references) <= 12:
        parser.error("Cobra 候选需要 1 到 12 张角色参考图")
    if args.candidate == "flux2-klein-4b" and len(references) != 3:
        parser.error("FLUX.2 当前候选工作流需要恰好 3 张角色参考图")
    if args.candidate == "flux2-klein-4b" and not args.workflow:
        parser.error("FLUX.2 候选必须提供 --workflow")

    workflow_path = args.workflow
    if args.candidate == "cobra" and workflow_path is None:
        workflow_path = PROJECT_ROOT / "workflows" / "cobra-colorize.json"
    workflow = json.loads(workflow_path.resolve().read_text(encoding="utf-8"))
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
                        reference_inputs,
                        workflow,
                        seed=args.seed + index - 1,
                        steps=args.cobra_steps,
                        top_k=args.cobra_top_k,
                        style=args.cobra_style,
                        timeout_seconds=args.timeout,
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
                if args.candidate == "cobra":
                    geometry_image = ComfyUIBackend._restore_geometry(
                        source_bytes,
                        raw_image,
                    )
                    protected_image = ComfyUIBackend._protect_cobra_structure(
                        source_bytes,
                        geometry_image,
                    )
                else:
                    geometry_image = raw_image
                    protected_image = ComfyUIBackend._protect_source_structure(
                        source_bytes,
                        geometry_image,
                    )
                raw_path = output_dir / f"page-{index:02d}-raw.png"
                geometry_path = output_dir / f"page-{index:02d}-geometry.png"
                protected_path = output_dir / f"page-{index:02d}-protected.png"
                raw_image.save(raw_path, format="PNG", optimize=True)
                geometry_image.save(geometry_path, format="PNG", optimize=True)
                protected_image.save(protected_path, format="PNG", optimize=True)
                geometry_stream = BytesIO()
                geometry_image.save(geometry_stream, format="PNG")
                protected_stream = BytesIO()
                protected_image.save(protected_stream, format="PNG")
                item = {
                    "page": str(page.relative_to(PROJECT_ROOT)),
                    "prompt_id": prompt_id,
                    "client_elapsed_ms": elapsed_ms,
                    "backend_elapsed_ms": backend_ms,
                    "raw_output": str(raw_path.relative_to(PROJECT_ROOT)),
                    "geometry_output": str(geometry_path.relative_to(PROJECT_ROOT)),
                    "protected_output": str(protected_path.relative_to(PROJECT_ROOT)),
                    "raw_metrics": image_metrics(source_bytes, raw_bytes),
                    "geometry_metrics": image_metrics(
                        source_bytes,
                        geometry_stream.getvalue(),
                    ),
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
        "reference_images": [
            str(reference.relative_to(PROJECT_ROOT)) for reference in references
        ],
        "workflow": str(workflow_path.resolve().relative_to(PROJECT_ROOT)),
        "parameters": (
            {
                "style": args.cobra_style,
                "steps": args.cobra_steps,
                "top_k": args.cobra_top_k,
                "seed": args.seed,
            }
            if args.candidate == "cobra"
            else {"seed": args.seed}
        ),
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
