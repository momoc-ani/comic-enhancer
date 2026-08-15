from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageFilter, ImageOps, ImageStat


REFERENCE_SELECTION_REVISION = "reference-view-v2"
REFERENCE_PROVIDER_PRIORITY = {
    "bangumi": 60,
    "anilist": 50,
    "mal": 40,
    "kitsu": 30,
    "shikimori": 20,
    "mangaupdates": 10,
}


@dataclass(frozen=True)
class ReferenceImageQuality:
    """记录参考图的尺寸、色彩、细节与构图评估。"""

    width: int
    height: int
    saturation: float
    detail: float
    colorful: bool
    full_body: bool
    usable: bool


# 方法说明：评估参考图的色彩、尺寸和构图质量。
def assess_reference_image(image_bytes: bytes) -> ReferenceImageQuality:
    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        sample = image.copy()
        sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
        saturation = ImageStat.Stat(sample.convert("HSV")).mean[1]
        grayscale = sample.convert("L")
        contrast = ImageStat.Stat(grayscale).stddev[0]
        detail = ImageStat.Stat(grayscale.filter(ImageFilter.FIND_EDGES)).mean[0]
        full_body = _looks_like_full_body_reference(sample)
    return ReferenceImageQuality(
        width=width,
        height=height,
        saturation=saturation,
        detail=detail,
        colorful=saturation >= 8.0,
        full_body=full_body,
        usable=min(width, height) >= 128 and contrast >= 5.0,
    )


# 方法说明：判断参考图是否近似完整人物立绘。
def _looks_like_full_body_reference(image: Image.Image) -> bool:
    sample = image.copy()
    sample.thumbnail((256, 256), Image.Resampling.LANCZOS)
    quantized = sample.quantize(colors=32)
    width, height = quantized.size
    border_width = max(2, round(min(width, height) * 0.06))
    pixels = list(quantized.getdata())
    border = [
        pixels[y * width + x]
        for y in range(height)
        for x in range(width)
        if x < border_width
        or x >= width - border_width
        or y < border_width
        or y >= height - border_width
    ]
    background_index = max(set(border), key=border.count)
    border_uniformity = border.count(background_index) / len(border)
    if border_uniformity < 0.65:
        return False

    palette = quantized.getpalette()
    background = tuple(palette[background_index * 3 : background_index * 3 + 3])
    foreground = [
        max(abs(channel - background[index]) for index, channel in enumerate(pixel))
        >= 28
        for pixel in sample.getdata()
    ]
    coverage = sum(foreground) / len(foreground)
    if not 0.12 <= coverage <= 0.75:
        return False
    foreground_rows = [
        y
        for y in range(height)
        if any(foreground[y * width : (y + 1) * width])
    ]
    vertical_extent = (foreground_rows[-1] - foreground_rows[0] + 1) / height
    return vertical_extent >= 0.80


# 方法说明：生成参考图质量排序键。
def reference_quality_rank(
    quality: ReferenceImageQuality,
    *,
    confirmed_source: bool,
    provider: str,
) -> tuple[int, int, int, int, int, int, float, float, int]:
    return (
        int(quality.usable),
        int(confirmed_source),
        int(quality.colorful),
        int(quality.full_body),
        min(quality.width, quality.height),
        quality.width * quality.height,
        round(quality.saturation, 1),
        quality.detail,
        REFERENCE_PROVIDER_PRIORITY.get(provider, 0),
    )
