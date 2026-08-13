from io import BytesIO

import pytest
from PIL import Image

from comic_enhancer.references import (
    ReferenceImageStore,
    assess_reference_image,
    reference_quality_rank,
)


def test_normalize_reference_limits_size():
    source = BytesIO()
    Image.new("RGB", (3000, 1500), "red").save(source, format="JPEG")

    normalized = ReferenceImageStore._normalize(source.getvalue())

    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (2048, 1024)
        assert image.mode == "RGB"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/cover.png",
        "http://localhost/cover.png",
    ],
)
def test_reference_url_rejects_non_public_targets(url):
    with pytest.raises(ValueError):
        ReferenceImageStore._validate_public_url(url)


def test_reference_quality_prefers_color_before_resolution():
    gray = BytesIO()
    Image.new("L", (1000, 1400), 128).save(gray, format="PNG")
    color = BytesIO()
    Image.new("RGB", (320, 480), (220, 40, 80)).save(color, format="PNG")

    gray_quality = assess_reference_image(gray.getvalue())
    color_quality = assess_reference_image(color.getvalue())

    assert reference_quality_rank(
        color_quality,
        confirmed_source=True,
        provider="anilist",
    ) > reference_quality_rank(
        gray_quality,
        confirmed_source=True,
        provider="bangumi",
    )


def test_reference_quality_prefers_larger_color_image():
    small = BytesIO()
    Image.new("RGB", (230, 345), (140, 80, 110)).save(small, format="PNG")
    large = BytesIO()
    Image.new("RGB", (690, 1050), (140, 80, 110)).save(large, format="PNG")

    small_quality = assess_reference_image(small.getvalue())
    large_quality = assess_reference_image(large.getvalue())

    assert reference_quality_rank(
        large_quality,
        confirmed_source=True,
        provider="bangumi",
    ) > reference_quality_rank(
        small_quality,
        confirmed_source=True,
        provider="anilist",
    )
