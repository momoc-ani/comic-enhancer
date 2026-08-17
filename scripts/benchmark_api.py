#!/usr/bin/env python3
"""Benchmark the authenticated Comic Enhancer API with a work manifest."""

from __future__ import annotations

import argparse
import colorsys
from datetime import datetime, timezone
import json
import os
from io import BytesIO
from pathlib import Path
import re
import statistics
import subprocess
import threading
import time

import httpx
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADMISSION_MIN_PAGES = 100
RESOURCE_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")
REMOTE_RESOURCE_COMMAND = r"""
gpu_line=$(
  nvidia-smi \
    --query-gpu=memory.used,memory.total,utilization.gpu \
    --format=csv,noheader,nounits |
    head -n 1
)
api_pid=$(docker inspect --format '{{.State.Pid}}' comic-enhancer-api 2>/dev/null || true)
api_rss_kib=0
if [ -n "$api_pid" ] && [ -r "/proc/$api_pid/status" ]; then
  api_rss_kib=$(awk '/^VmRSS:/ {print $2}' "/proc/$api_pid/status")
fi
printf '%s|%s\n' "$gpu_line" "$api_rss_kib"
""".strip()


# 方法说明：将路径格式化为便于展示的相对路径。
def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# 方法说明：计算原图与结果图的质量和结构指标。
def image_metrics(source_bytes: bytes, result_bytes: bytes) -> dict[str, object]:
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("L")
    with Image.open(BytesIO(result_bytes)) as result_file:
        result_rgb = ImageOps.exif_transpose(result_file).convert("RGB")
        result_size = result_rgb.size
        result = result_rgb.convert("L").resize(source.size, Image.Resampling.LANCZOS)

    dark_pixel_count = 0
    retained_dark_pixels = 0
    mid_dark_pixel_count = 0
    retained_mid_dark_pixels = 0
    white_pixel_count = 0
    retained_white_pixels = 0
    luminance_difference = 0
    pixel_count = source.width * source.height
    for source_value, result_value in zip(source.getdata(), result.getdata()):
        luminance_difference += abs(source_value - result_value)
        if source_value <= 48:
            dark_pixel_count += 1
            retained_dark_pixels += result_value <= 80
        if source_value <= 96:
            mid_dark_pixel_count += 1
            retained_mid_dark_pixels += result_value <= 112
        if source_value >= 245:
            white_pixel_count += 1
            retained_white_pixels += result_value >= 230

    color_sample = result_rgb.copy()
    color_sample.thumbnail((320, 320), Image.Resampling.LANCZOS)
    hue_bins = [0] * 12
    saturated_pixel_count = 0
    color_sample_pixels = color_sample.width * color_sample.height
    for red, green, blue in color_sample.getdata():
        hue, saturation, value = colorsys.rgb_to_hsv(
            red / 255,
            green / 255,
            blue / 255,
        )
        if saturation < 0.10 or value < 0.18:
            continue
        saturated_pixel_count += 1
        hue_bins[min(11, int(hue * 12))] += 1
    active_threshold = max(16, round(saturated_pixel_count * 0.015))
    active_hue_bins = sum(count >= active_threshold for count in hue_bins)
    dominant_hue_ratio = (
        max(hue_bins, default=0) / saturated_pixel_count
        if saturated_pixel_count
        else 0
    )
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
        "mid_dark_pixel_retention": round(
            retained_mid_dark_pixels / max(1, mid_dark_pixel_count),
            4,
        ),
        "white_pixel_retention": round(
            retained_white_pixels / max(1, white_pixel_count),
            4,
        ),
        "luminance_mae": round(luminance_difference / max(1, pixel_count), 3),
        "saturated_pixel_ratio": round(
            saturated_pixel_count / max(1, color_sample_pixels),
            4,
        ),
        "active_hue_bins": active_hue_bins,
        "dominant_hue_ratio": round(dominant_hue_ratio, 4),
        "hue_bins": hue_bins,
    }


# 方法说明：按最近秩规则计算指定百分位数。
def percentile(values: list[int], percentile_value: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, (percentile_value * len(ordered) + 99) // 100 - 1))
    return ordered[rank]


# 方法说明：汇总基准测试结果与统计指标。
def summarize(
    results: list[dict[str, object]],
    failures: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    failures = failures or []
    client_times = [int(result["client_elapsed_ms"]) for result in results]
    service_times = [int(result["service_elapsed_ms"]) for result in results]
    dark_retention = [
        float(result["metrics"]["dark_pixel_retention"])
        for result in results
    ]
    mid_dark_retention = [
        float(result["metrics"].get("mid_dark_pixel_retention", 0))
        for result in results
        if "mid_dark_pixel_retention" in result["metrics"]
    ]
    white_retention = [
        float(result["metrics"].get("white_pixel_retention", 0))
        for result in results
        if "white_pixel_retention" in result["metrics"]
    ]
    luminance_mae = [float(result["metrics"].get("luminance_mae", 0)) for result in results]
    saturated_ratios = [
        float(result["metrics"].get("saturated_pixel_ratio", 0))
        for result in results
    ]
    active_hue_bins = [
        int(result["metrics"].get("active_hue_bins", 0))
        for result in results
    ]
    dominant_hue_ratios = [
        float(result["metrics"].get("dominant_hue_ratio", 0))
        for result in results
        if float(result["metrics"].get("saturated_pixel_ratio", 0)) >= 0.005
    ]
    scale_x = [
        float(result["metrics"]["scale_x"])
        for result in results
        if "scale_x" in result["metrics"]
    ]
    scale_y = [
        float(result["metrics"]["scale_y"])
        for result in results
        if "scale_y" in result["metrics"]
    ]
    total_pages = len(results) + len(failures)
    return {
        "pages": total_pages,
        "successful_pages": len(results),
        "failed_pages": len(failures),
        "failure_rate": round(len(failures) / max(1, total_pages), 4),
        "cache_hits": sum(bool(result["cached"]) for result in results),
        "client_p50_ms": round(statistics.median(client_times)) if client_times else 0,
        "client_p95_ms": percentile(client_times, 95),
        "service_p50_ms": round(statistics.median(service_times)) if service_times else 0,
        "service_p95_ms": percentile(service_times, 95),
        "minimum_dark_pixel_retention": min(dark_retention, default=0),
        "minimum_mid_dark_pixel_retention": min(mid_dark_retention, default=0),
        "minimum_white_pixel_retention": min(white_retention, default=0),
        "luminance_mae_p95": (
            percentile([round(value * 1000) for value in luminance_mae], 95) / 1000
            if luminance_mae
            else 0
        ),
        "median_saturated_pixel_ratio": (
            round(statistics.median(saturated_ratios), 4)
            if saturated_ratios
            else 0
        ),
        "median_active_hue_bins": (
            round(statistics.median(active_hue_bins), 2)
            if active_hue_bins
            else 0
        ),
        "maximum_dominant_hue_ratio": max(dominant_hue_ratios, default=0),
        "minimum_scale_x": min(scale_x, default=0),
        "maximum_scale_x": max(scale_x, default=0),
        "minimum_scale_y": min(scale_y, default=0),
        "maximum_scale_y": max(scale_y, default=0),
        "model_profiles": sorted({str(result["model_profile"]) for result in results}),
        "reference_pages": sum(bool(result["reference_applied"]) for result in results),
        "reference_effective_rate": round(
            sum(bool(result["reference_applied"]) for result in results)
            / max(1, len(results)),
            4,
        ),
        "processed_panels": sum(int(result["processed_panels"]) for result in results),
    }


# 方法说明：解析一行远端资源采样数据。
def parse_resource_sample_line(line: str) -> dict[str, float]:
    gpu_part, rss_part = line.strip().split("|", 1)
    gpu_values = [float(value.strip()) for value in gpu_part.split(",")]
    if len(gpu_values) != 3:
        raise ValueError("resource sampler returned invalid GPU fields")
    return {
        "gpu_memory_used_mib": gpu_values[0],
        "gpu_memory_total_mib": gpu_values[1],
        "gpu_utilization_percent": gpu_values[2],
        "api_rss_mib": round(float(rss_part.strip() or 0) / 1024, 3),
    }


# 方法说明：采集远端主机的 CPU、内存和显存指标。
def sample_remote_resources(host: str, label: str, page_index: int | None) -> dict[str, object]:
    sample: dict[str, object] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "page_index": page_index,
    }
    try:
        completed = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, REMOTE_RESOURCE_COMMAND],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        sample.update(parse_resource_sample_line(completed.stdout))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sample["error"] = str(error)
    return sample


class RemoteResourceMonitor:
    # 方法说明：初始化当前对象及其运行状态。
    def __init__(self, host: str, interval_seconds: float):
        self.host = host
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, object]] = []
        self.page_index: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    # 方法说明：启动后台资源采样线程。
    def start(self) -> None:
        self._thread.start()

    # 方法说明：更新资源采样关联的当前页码。
    def set_page_index(self, page_index: int | None) -> None:
        self.page_index = page_index

    # 方法说明：停止资源采样并返回已收集的数据。
    def stop(self) -> list[dict[str, object]]:
        self._stop.set()
        self._thread.join(timeout=20)
        if self._thread.is_alive():
            self.samples.append(
                {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "label": "monitor_stop",
                    "page_index": self.page_index,
                    "error": "resource monitor did not stop within 20 seconds",
                }
            )
        self.samples.append(sample_remote_resources(self.host, "after", None))
        return self.samples

    # 方法说明：循环采集资源指标直到收到停止信号。
    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(
                sample_remote_resources(self.host, "interval", self.page_index)
            )
            if self._stop.wait(self.interval_seconds):
                break


# 方法说明：汇总远端资源采样的峰值和增量。
def summarize_resources(samples: list[dict[str, object]]) -> dict[str, object]:
    valid = [sample for sample in samples if "error" not in sample]
    if not valid:
        return {
            "samples": len(samples),
            "valid_samples": 0,
            "errors": [
                str(item["error"])
                for item in samples
                if item.get("error")
            ],
        }

    # 方法说明：提取指定资源指标的有效采样值。
    def values(name: str) -> list[float]:
        return [float(sample[name]) for sample in valid]

    gpu_memory = values("gpu_memory_used_mib")
    api_rss = values("api_rss_mib")
    return {
        "samples": len(samples),
        "valid_samples": len(valid),
        "gpu_memory_used_min_mib": min(gpu_memory),
        "gpu_memory_used_max_mib": max(gpu_memory),
        "gpu_memory_growth_mib": round(gpu_memory[-1] - gpu_memory[0], 3),
        "gpu_utilization_max_percent": max(values("gpu_utilization_percent")),
        "api_rss_min_mib": min(api_rss),
        "api_rss_max_mib": max(api_rss),
        "api_rss_growth_mib": round(api_rss[-1] - api_rss[0], 3),
        "errors": [str(item["error"]) for item in samples if item.get("error")],
    }


# 方法说明：根据质量与性能门槛判定基准结果。
def evaluate_admission(
    *,
    manifest: dict[str, object],
    mode: str,
    phase: str,
    summary: dict[str, object],
    resource_summary: dict[str, object] | None,
    minimum_pages: int = DEFAULT_ADMISSION_MIN_PAGES,
) -> dict[str, object]:
    reasons = []
    if int(summary["pages"]) < minimum_pages:
        reasons.append(f"requires at least {minimum_pages} pages")
    if not bool(manifest.get("admission_eligible", False)):
        reasons.append("manifest is not marked admission_eligible")
    if reasons:
        return {
            "status": "smoke_only",
            "eligible": False,
            "minimum_pages": minimum_pages,
            "reasons": reasons,
            "checks": [],
        }

    checks = [
        _admission_check("all_pages_succeeded", int(summary["failed_pages"]), "eq", 0),
        _admission_check(
            "dark_pixel_retention",
            float(summary["minimum_dark_pixel_retention"]),
            "gte",
            0.99,
        ),
        _admission_check(
            "mid_dark_pixel_retention",
            float(summary["minimum_mid_dark_pixel_retention"]),
            "gte",
            0.98,
        ),
        _admission_check(
            "white_pixel_retention",
            float(summary["minimum_white_pixel_retention"]),
            "gte",
            0.98,
        ),
        _admission_check("luminance_mae_p95", float(summary["luminance_mae_p95"]), "lte", 8.0),
    ]
    if mode == "quality":
        checks.extend(
            [
                _admission_check(
                    "median_active_hue_bins",
                    float(summary["median_active_hue_bins"]),
                    "gte",
                    3,
                ),
                _admission_check(
                    "maximum_dominant_hue_ratio",
                    float(summary["maximum_dominant_hue_ratio"]),
                    "lte",
                    0.80,
                ),
            ]
        )
    if mode == "upscale":
        checks.extend(
            [
                _admission_check(
                    "model_profiles",
                    summary["model_profiles"],
                    "eq",
                    ["realcugan-se-2x"],
                ),
                _admission_check("minimum_scale_x", summary["minimum_scale_x"], "eq", 2),
                _admission_check("maximum_scale_x", summary["maximum_scale_x"], "eq", 2),
                _admission_check("minimum_scale_y", summary["minimum_scale_y"], "eq", 2),
                _admission_check("maximum_scale_y", summary["maximum_scale_y"], "eq", 2),
            ]
        )
    if phase == "warm" and mode == "fast":
        checks.extend(
            [
                _admission_check("client_p50_ms", int(summary["client_p50_ms"]), "lte", 2500),
                _admission_check("client_p95_ms", int(summary["client_p95_ms"]), "lte", 4000),
            ]
        )
    if phase == "cache":
        checks.extend(
            [
                _admission_check(
                    "all_pages_cached",
                    int(summary["cache_hits"]),
                    "eq",
                    int(summary["successful_pages"]),
                ),
                _admission_check("cache_client_p95_ms", int(summary["client_p95_ms"]), "lte", 300),
            ]
        )
    if resource_summary and int(resource_summary.get("valid_samples", 0)) >= 2:
        checks.extend(
            [
                _admission_check(
                    "gpu_memory_growth_mib",
                    float(resource_summary["gpu_memory_growth_mib"]),
                    "lte",
                    512,
                ),
                _admission_check(
                    "api_rss_growth_mib",
                    float(resource_summary["api_rss_growth_mib"]),
                    "lte",
                    256,
                ),
            ]
        )
    passed = all(bool(check["passed"]) for check in checks)
    return {
        "status": "passed" if passed else "failed",
        "eligible": True,
        "minimum_pages": minimum_pages,
        "reasons": [],
        "checks": checks,
    }


# 方法说明：生成单项准入检查结果。
def _admission_check(
    name: str,
    actual: object,
    operator: str,
    expected: object,
) -> dict[str, object]:
    passed = {
        "eq": actual == expected,
        "gte": actual >= expected,
        "lte": actual <= expected,
    }[operator]
    return {
        "name": name,
        "actual": actual,
        "operator": operator,
        "expected": expected,
        "passed": passed,
    }


# 方法说明：读取基准清单并解析漫画页路径。
def load_manifest(path: Path) -> tuple[dict[str, object], list[Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pages = [(PROJECT_ROOT / item).resolve() for item in payload["test_pages"]]
    missing = [str(page) for page in pages if not page.is_file()]
    if missing:
        raise FileNotFoundError("missing benchmark pages: " + ", ".join(missing))
    return payload, pages


# 方法说明：处理单页图片并返回统一结果。
def process_page(
    client: httpx.Client,
    headers: dict[str, str],
    work: dict[str, object],
    page: Path,
    page_index: int,
    mode: str,
    run_id: str,
    palette_version: str,
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
                        "palette_version": palette_version,
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
        "requested_mode": mode,
        "source": display_path(page),
        "output": str(output_path),
        "client_elapsed_ms": client_elapsed_ms,
        "service_elapsed_ms": process_result["elapsed_ms"],
        "cached": process_result["cached"],
        "model_profile": process_result["model_profile"],
        "reference_applied": process_result["reference_applied"],
        "processed_panels": process_result["processed_panels"],
        "metrics": image_metrics(source_bytes, image_response.content),
    }


# 方法说明：为处理失败的页面生成统一结果记录。
def failure_payload(page: Path, page_index: int, error: Exception) -> dict[str, object]:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    detail = ""
    if response is not None:
        try:
            detail = response.text[:500]
        except Exception:
            detail = ""
    return {
        "page_index": page_index,
        "source": display_path(page),
        "error_type": type(error).__name__,
        "status_code": status_code,
        "reason": detail or str(error),
    }


# 方法说明：解析命令行参数并执行程序主流程。
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "fast",
            "quality",
            "upscale",
            "flux2",
            "flux2_quant",
            "flux2_9b_lora",
            "flux2_4b_source",
            "flux2_4b_color",
        ),
        required=True,
    )
    parser.add_argument(
        "--phase",
        choices=("cold", "warm", "cache"),
        default="warm",
        help="Label this run without restarting model services automatically.",
    )
    parser.add_argument(
        "--palette-version",
        default="",
        help="Use the same value across runs when measuring cache hits.",
    )
    parser.add_argument(
        "--resource-ssh-host",
        default="",
        help="Optional SSH host for fixed read-only GPU and API RSS sampling.",
    )
    parser.add_argument("--resource-sample-interval", type=float, default=2.0)
    parser.add_argument("--admission-min-pages", type=int, default=DEFAULT_ADMISSION_MIN_PAGES)
    parser.add_argument("--token-env", default="COMIC_ENHANCER_TOKEN")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/benchmarks/api"))
    args = parser.parse_args()

    token = os.environ.get(args.token_env, "")
    if not token:
        parser.error(f"environment variable {args.token_env} is required")
    manifest, pages = load_manifest(args.manifest.resolve())
    if args.resource_ssh_host and not RESOURCE_HOST_PATTERN.fullmatch(args.resource_ssh_host):
        parser.error("--resource-ssh-host contains unsupported characters")
    if args.admission_min_pages < 1:
        parser.error("--admission-min-pages must be positive")
    if args.resource_sample_interval < 0.5:
        parser.error("--resource-sample-interval must be at least 0.5 seconds")
    work = manifest["work"]
    run_id = time.strftime("%Y%m%d-%H%M%S")
    palette_version = args.palette_version or (
        f"benchmark-cache-{manifest['benchmark_id']}-{args.mode}"
        if args.phase == "cache"
        else f"benchmark-{run_id}"
    )
    output_dir = args.output_dir.resolve() / f"{manifest['benchmark_id']}-{args.mode}-{run_id}"
    output_dir.mkdir(parents=True, exist_ok=False)
    headers = {"Authorization": f"Bearer {token}"}

    report: dict[str, object] = {
        "schema_version": 2,
        "benchmark_id": manifest["benchmark_id"],
        "rights_status": manifest.get("rights_status", "unspecified"),
        "mode": args.mode,
        "phase": args.phase,
        "palette_version": palette_version,
        "base_url": args.base_url.rstrip("/"),
        "run_id": run_id,
        "pages": [],
        "failures": [],
        "resource_samples": [],
    }
    monitor = (
        RemoteResourceMonitor(args.resource_ssh_host, args.resource_sample_interval)
        if args.resource_ssh_host
        else None
    )
    if monitor:
        monitor.start()
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
            capabilities = client.get("/v1/capabilities", headers=headers)
            capabilities.raise_for_status()
            report["capabilities"] = capabilities.json()
            for page_index, page in enumerate(pages):
                if monitor:
                    monitor.set_page_index(page_index)
                try:
                    result = process_page(
                        client,
                        headers,
                        work,
                        page,
                        page_index,
                        args.mode,
                        run_id,
                        palette_version,
                        output_dir,
                    )
                except (httpx.HTTPError, OSError, ValueError) as error:
                    failure = failure_payload(page, page_index, error)
                    report["failures"].append(failure)
                    print(f"第 {page_index + 1} 页失败: {failure['reason']}", flush=True)
                else:
                    report["pages"].append(result)
                    print(
                        f"第 {page_index + 1} 页: {result['client_elapsed_ms']} ms "
                        f"{result['model_profile']}",
                        flush=True,
                    )
    finally:
        if monitor:
            report["resource_samples"] = monitor.stop()

    report["summary"] = summarize(report["pages"], report["failures"])
    report["resource_summary"] = summarize_resources(report["resource_samples"])
    report["admission"] = evaluate_admission(
        manifest=manifest,
        mode=args.mode,
        phase=args.phase,
        summary=report["summary"],
        resource_summary=report["resource_summary"],
        minimum_pages=args.admission_min_pages,
    )
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(report["admission"], ensure_ascii=False, indent=2))
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
