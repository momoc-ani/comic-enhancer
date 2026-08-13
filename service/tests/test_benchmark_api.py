from io import BytesIO

from PIL import Image

from scripts.benchmark_api import image_metrics, percentile, summarize


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
    assert metrics["luminance_mae"] < 1


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
    assert summary["cache_hits"] == 1
    assert summary["client_p50_ms"] == 1600
    assert summary["client_p95_ms"] == 2100
    assert summary["minimum_dark_pixel_retention"] == 0.97
    assert summary["model_profiles"] == ["manganinja-reference", "sd15-colorize"]
    assert summary["reference_pages"] == 1
    assert summary["processed_panels"] == 2
