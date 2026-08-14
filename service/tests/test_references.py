from io import BytesIO

import pytest
from PIL import Image

from comic_enhancer.references import (
    ReferenceImageStore,
    assess_reference_image,
    reference_quality_rank,
)


# 方法说明：验证参考图规范化会限制最大尺寸。
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
# 方法说明：验证参考图地址会拒绝非公网目标。
def test_reference_url_rejects_non_public_targets(url):
    with pytest.raises(ValueError):
        ReferenceImageStore._validate_public_url(url)


# 方法说明：验证彩色参考图的质量排名高于灰度图。
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


# 方法说明：验证同为彩色时优先选择更大分辨率参考图。
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


# 方法说明：验证完整人物构图优先于鲜艳但裁切的肖像。
def test_reference_quality_prefers_full_body_composition_over_vivid_portrait():
    portrait = Image.new("RGB", (230, 345), (220, 40, 80))
    portrait_source = BytesIO()
    portrait.save(portrait_source, format="PNG")

    full_body = Image.new("RGB", (690, 1050), "white")
    for y in range(60, 1020):
        half_width = 75 if y < 300 else 145
        for x in range(345 - half_width, 345 + half_width):
            full_body.putpixel((x, y), (80, 90, 130))
    full_body_source = BytesIO()
    full_body.save(full_body_source, format="PNG")

    portrait_quality = assess_reference_image(portrait_source.getvalue())
    full_body_quality = assess_reference_image(full_body_source.getvalue())

    assert portrait_quality.full_body is False
    assert full_body_quality.full_body is True
    assert reference_quality_rank(
        full_body_quality,
        confirmed_source=True,
        provider="bangumi",
    ) > reference_quality_rank(
        portrait_quality,
        confirmed_source=True,
        provider="anilist",
    )
