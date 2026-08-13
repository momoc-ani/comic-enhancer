#!/usr/bin/env python3
"""Benchmark the authenticated Comic Enhancer API with a work manifest."""

from __future__ import annotations

import argparse
import json
import os
from io import BytesIO
from pathlib import Path
import statistics
import time

import httpx
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def image_metrics(source_bytes: bytes, result_bytes: bytes) -> dict[str, object]:
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("L")
    with Image.open(BytesIO(result_bytes)) as result_file:
        result_rgb = ImageOps.exif_transpose(result_file).convert("RGB")
        result_size = result_rgb.size
        result = result_rgb.convert("L").resize(source.size, Image.Resampling.LANCZOS)

    dark_pixel_count = 0
    retained_dark_pixels = 0
    luminance_difference = 0
    pixel_count = source.width * source.height
    for source_value, result_value in zip(source.getdata(), result.getdata()):
        luminance_difference += abs(source_value - result_value)
        if source_value <= 48:
            dark_pixel_count += 1
            retained_dark_pixels += result_value <= 80
    return {
        "source_size": list(source.size),
        "result_size": list(result_size),
        "scale_x": round(result_size[0] / source.width, 4),
        "scale_y": round(result_size[1] / source.height, 4),
        "dark_pixel_count": dark_pixel_count,
        "dark_pixel_retention": round(
            retained_dark_pixels / max(1, dark_pixel_count),
            4,
        ),
        "luminance_mae": round(luminance_difference / max(1, pixel_count), 3),
    }


def percentile(values: list[int], percentile_value: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, (percentile_value * len(ordered) + 99) // 100 - 1))
    return ordered[rank]


def summarize(results: list[dict[str, object]]) -> dict[str, object]:
    client_times = [int(result["client_elapsed_ms"]) for result in results]
    service_times = [int(result["service_elapsed_ms"]) for result in results]
    dark_retention = [
        float(result["metrics"]["dark_pixel_retention"])
        for result in results
    ]
    return {
        "pages": len(results),
        "cache_hits": sum(bool(result["cached"]) for result in results),
        "client_p50_ms": round(statistics.median(client_times)) if client_times else 0,
        "client_p95_ms": percentile(client_times, 95),
        "service_p50_ms": round(statistics.median(service_times)) if service_times else 0,
        "service_p95_ms": percentile(service_times, 95),
        "minimum_dark_pixel_retention": min(dark_retention, default=0),
        "model_profiles": sorted({str(result["model_profile"]) for result in results}),
        "adapter_sources": sorted({str(result["adapter_source"]) for result in results}),
        "reference_pages": sum(bool(result["reference_applied"]) for result in results),
        "processed_panels": sum(int(result["processed_panels"]) for result in results),
    }


def load_manifest(path: Path) -> tuple[dict[str, object], list[Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pages = [(PROJECT_ROOT / item).resolve() for item in payload["test_pages"]]
    missing = [str(page) for page in pages if not page.is_file()]
    if missing:
        raise FileNotFoundError("missing benchmark pages: " + ", ".join(missing))
    return payload, pages


def analyze_pages(
    client: httpx.Client,
    headers: dict[str, str],
    work: dict[str, object],
    pages: list[Path],
) -> tuple[int, dict[str, object] | None]:
    files = []
    handles = []
    try:
        for page in pages:
            handle = page.open("rb")
            handles.append(handle)
            files.append(("pages", (page.name, handle, "application/octet-stream")))
        started = time.perf_counter()
        response = client.post(
            "/v1/pages/analyze",
            headers=headers,
            data={"work_json": json.dumps(work, ensure_ascii=False)},
            files=files,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
    finally:
        for handle in handles:
            handle.close()
    if response.status_code == 409:
        return elapsed_ms, None
    response.raise_for_status()
    return elapsed_ms, response.json()


def process_page(
    client: httpx.Client,
    headers: dict[str, str],
    work: dict[str, object],
    page: Path,
    page_index: int,
    mode: str,
    run_id: str,
    output_dir: Path,
) -> dict[str, object]:
    source_bytes = page.read_bytes()
    with page.open("rb") as stream:
        started = time.perf_counter()
        response = client.post(
            "/v1/pages/process",
            headers=headers,
            data={
                "work_json": json.dumps(work, ensure_ascii=False),
                "options_json": json.dumps(
                    {
                        "mode": mode,
                        "page_index": page_index,
                        "palette_version": f"benchmark-{run_id}",
                    }
                ),
            },
            files={"image": (page.name, stream, "application/octet-stream")},
        )
        client_elapsed_ms = round((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    process_result = response.json()
    image_response = client.get(process_result["result_url"], headers=headers)
    image_response.raise_for_status()
    output_path = output_dir / f"page-{page_index + 1:03d}.webp"
    output_path.write_bytes(image_response.content)
    return {
        "page_index": page_index,
        "source": str(page.relative_to(PROJECT_ROOT)),
        "output": str(output_path),
        "client_elapsed_ms": client_elapsed_ms,
        "service_elapsed_ms": process_result["elapsed_ms"],
        "cached": process_result["cached"],
        "model_profile": process_result["model_profile"],
        "adapter_source": process_result["adapter_source"],
        "adapter_id": process_result["adapter_id"],
        "adapter_applied": process_result["adapter_applied"],
        "reference_applied": process_result["reference_applied"],
        "processed_panels": process_result["processed_panels"],
        "metrics": image_metrics(source_bytes, image_response.content),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=("fast", "quality"), required=True)
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--token-env", default="COMIC_ENHANCER_TOKEN")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/benchmarks/api"))
    args = parser.parse_args()

    token = os.environ.get(args.token_env, "")
    if not token:
        parser.error(f"environment variable {args.token_env} is required")
    manifest, pages = load_manifest(args.manifest.resolve())
    work = manifest["work"]
    run_id = time.strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir.resolve() / f"{manifest['benchmark_id']}-{args.mode}-{run_id}"
    output_dir.mkdir(parents=True, exist_ok=False)
    headers = {"Authorization": f"Bearer {token}"}

    report: dict[str, object] = {
        "schema_version": 1,
        "benchmark_id": manifest["benchmark_id"],
        "rights_status": manifest.get("rights_status", "unspecified"),
        "mode": args.mode,
        "base_url": args.base_url.rstrip("/"),
        "run_id": run_id,
        "analysis": None,
        "pages": [],
    }
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        capabilities = client.get("/v1/capabilities", headers=headers)
        capabilities.raise_for_status()
        report["capabilities"] = capabilities.json()
        if args.analyze:
            analysis_elapsed_ms, analysis = analyze_pages(client, headers, work, pages)
            report["analysis"] = {
                "elapsed_ms": analysis_elapsed_ms,
                "available": analysis is not None,
                "profile": analysis.get("analyzer_profile") if analysis else None,
                "pages": len(analysis.get("pages", [])) if analysis else 0,
            }
        for page_index, page in enumerate(pages):
            result = process_page(
                client,
                headers,
                work,
                page,
                page_index,
                args.mode,
                run_id,
                output_dir,
            )
            report["pages"].append(result)
            print(
                f"第 {page_index + 1} 页: {result['client_elapsed_ms']} ms "
                f"{result['model_profile']} {result['adapter_source']}",
                flush=True,
            )

    report["summary"] = summarize(report["pages"])
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
