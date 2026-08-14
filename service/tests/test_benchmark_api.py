from io import BytesIO

from PIL import Image

from scripts.benchmark_api import (
    evaluate_admission,
    image_metrics,
    parse_resource_sample_line,
    percentile,
    summarize,
    summarize_resources,
)


def image_bytes(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_image_metrics_measure_dimensions_and_dark_pixel_retention():
    source = Image.new("RGB", (10, 20), "white")
    source.putpixel((4, 5), (0, 0, 0))
    result = source.resize((20, 40), Image.Resampling.NEAREST)

    metrics = image_metrics(image_bytes(source), image_bytes(result))

    assert metrics["source_size"] == [10, 20]
    assert metrics["result_size"] == [20, 40]
    assert metrics["scale_x"] == 2
    assert metrics["scale_y"] == 2
    assert metrics["dark_pixel_retention"] == 1
    assert metrics["mid_dark_pixel_retention"] == 1
    assert metrics["white_pixel_retention"] == 1
    assert metrics["luminance_mae"] < 1
    assert metrics["saturated_pixel_ratio"] == 0


def test_image_metrics_reports_multiple_hues_and_dominant_color_bias():
    source = Image.new("RGB", (120, 120), "white")
    diverse = Image.new("RGB", source.size, "red")
    for x in range(40, 80):
        for y in range(120):
            diverse.putpixel((x, y), (0, 255, 0))
    for x in range(80, 120):
        for y in range(120):
            diverse.putpixel((x, y), (0, 0, 255))
    biased = Image.new("RGB", source.size, (0, 255, 255))

    diverse_metrics = image_metrics(image_bytes(source), image_bytes(diverse))
    biased_metrics = image_metrics(image_bytes(source), image_bytes(biased))

    assert diverse_metrics["active_hue_bins"] >= 3
    assert diverse_metrics["dominant_hue_ratio"] < 0.5
    assert biased_metrics["active_hue_bins"] == 1
    assert biased_metrics["dominant_hue_ratio"] == 1


def test_percentile_uses_nearest_rank():
    assert percentile([100, 200, 300], 50) == 200
    assert percentile([100, 200, 300], 95) == 300
    assert percentile([], 95) == 0


def test_summary_reports_actual_model_and_fallback_state():
    results = [
        {
            "client_elapsed_ms": 1100,
            "service_elapsed_ms": 1000,
            "cached": False,
            "model_profile": "sd15-colorize",
            "adapter_source": "none",
            "reference_applied": False,
            "processed_panels": 0,
            "metrics": {"dark_pixel_retention": 0.99},
        },
        {
            "client_elapsed_ms": 2100,
            "service_elapsed_ms": 2000,
            "cached": True,
            "model_profile": "manganinja-reference",
            "adapter_source": "work",
            "reference_applied": True,
            "processed_panels": 2,
            "metrics": {"dark_pixel_retention": 0.97},
        },
    ]

    summary = summarize(results)

    assert summary["pages"] == 2
    assert summary["successful_pages"] == 2
    assert summary["failed_pages"] == 0
    assert summary["cache_hits"] == 1
    assert summary["client_p50_ms"] == 1600
    assert summary["client_p95_ms"] == 2100
    assert summary["minimum_dark_pixel_retention"] == 0.97
    assert summary["model_profiles"] == ["manganinja-reference", "sd15-colorize"]
    assert summary["reference_pages"] == 1
    assert summary["processed_panels"] == 2


def test_summary_keeps_failures_and_manganinja_fallback_visible():
    results = [
        {
            "client_elapsed_ms": 100,
            "service_elapsed_ms": 90,
            "cached": False,
            "model_profile": "sd15-colorize",
            "adapter_source": "none",
            "reference_applied": False,
            "processed_panels": 0,
            "requested_mode": "manganinja",
            "metrics": {
                "dark_pixel_retention": 1,
                "mid_dark_pixel_retention": 1,
                "white_pixel_retention": 1,
                "luminance_mae": 1,
                "saturated_pixel_ratio": 0.02,
                "active_hue_bins": 4,
                "dominant_hue_ratio": 0.4,
            },
        }
    ]

    summary = summarize(results, [{"page_index": 1, "reason": "timeout"}])

    assert summary["pages"] == 2
    assert summary["failed_pages"] == 1
    assert summary["failure_rate"] == 0.5
    assert summary["fallback_pages"] == 1
    assert summary["fallback_rate"] == 1


def test_resource_samples_parse_and_report_growth():
    first = parse_resource_sample_line("10000, 24564, 17|204800")
    second = parse_resource_sample_line("10300, 24564, 21|230400")
    first.update({"label": "before"})
    second.update({"label": "after"})

    summary = summarize_resources([first, second])

    assert summary["gpu_memory_growth_mib"] == 300
    assert summary["api_rss_growth_mib"] == 25
    assert summary["gpu_utilization_max_percent"] == 21


def test_three_page_manifest_can_only_produce_smoke_result():
    admission = evaluate_admission(
        manifest={"admission_eligible": False},
        mode="manganinja",
        phase="warm",
        summary={"pages": 3},
        resource_summary=None,
    )

    assert admission["status"] == "smoke_only"
    assert admission["eligible"] is False
    assert admission["checks"] == []


def test_eligible_dataset_fails_machine_gate_when_color_is_one_note():
    admission = evaluate_admission(
        manifest={"admission_eligible": True},
        mode="quality",
        phase="warm",
        summary={
            "pages": 100,
            "failed_pages": 0,
            "minimum_dark_pixel_retention": 1,
            "minimum_mid_dark_pixel_retention": 1,
            "minimum_white_pixel_retention": 1,
            "luminance_mae_p95": 2,
            "median_active_hue_bins": 1,
            "maximum_dominant_hue_ratio": 0.95,
        },
        resource_summary=None,
    )

    assert admission["status"] == "failed"
    failed = {item["name"] for item in admission["checks"] if not item["passed"]}
    assert failed == {"median_active_hue_bins", "maximum_dominant_hue_ratio"}
