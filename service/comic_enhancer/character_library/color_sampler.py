from __future__ import annotations

from collections import Counter
from io import BytesIO

from PIL import Image, ImageOps

from ..character_vision import ProfileRegion
from .models import CharacterColorEvidence


# 方法说明：将第三方参考图规范化为内容寻址存储使用的 RGB PNG。
def normalize_reference_image(image_bytes: bytes) -> bytes:
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except (OSError, ValueError) as error:
        raise ValueError("角色参考图无法解码") from error


# 方法说明：从 VLM 定位区域内确定性提取主色，不采用模型生成的颜色文本。
def sample_profile_colors(
    image_bytes: bytes,
    regions: list[ProfileRegion],
    *,
    min_confidence: float = 0.65,
) -> list[CharacterColorEvidence]:
    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        colors: list[CharacterColorEvidence] = []
        seen_parts: set[str] = set()
        for region in sorted(regions, key=lambda item: item.confidence, reverse=True):
            if region.confidence < min_confidence or region.part in seen_parts:
                continue
            rgb = _sample_region(image, region.box_2d)
            if rgb is None:
                continue
            seen_parts.add(region.part)
            colors.append(
                CharacterColorEvidence(
                    part=region.part,
                    rgb=rgb,
                    confidence=region.confidence,
                )
            )
    return colors


# 方法说明：在千分比矩形的内部区域统计量化主色。
def _sample_region(
    image: Image.Image,
    box_2d: tuple[int, int, int, int],
) -> tuple[int, int, int] | None:
    width, height = image.size
    x1, y1, x2, y2 = box_2d
    left = max(0, min(width - 1, round(x1 * width / 1000)))
    top = max(0, min(height - 1, round(y1 * height / 1000)))
    right = max(left + 1, min(width, round(x2 * width / 1000)))
    bottom = max(top + 1, min(height, round(y2 * height / 1000)))
    crop = image.crop((left, top, right, bottom))
    if crop.width * crop.height < 9:
        return None
    crop.thumbnail((128, 128), Image.Resampling.LANCZOS)
    pixels = list(crop.getdata())
    useful = [
        pixel
        for pixel in pixels
        if not (
            max(pixel) >= 245
            and min(pixel) >= 235
            and max(pixel) - min(pixel) <= 12
        )
        and max(pixel) > 6
    ]
    if len(useful) < max(3, len(pixels) // 20):
        useful = [pixel for pixel in pixels if max(pixel) > 6]
    if not useful:
        return None
    buckets = Counter(tuple(channel // 24 for channel in pixel) for pixel in useful)
    selected_bucket, _ = buckets.most_common(1)[0]
    selected = [
        pixel
        for pixel in useful
        if tuple(channel // 24 for channel in pixel) == selected_bucket
    ]
    return tuple(
        sorted(pixel[channel] for pixel in selected)[len(selected) // 2]
        for channel in range(3)
    )
