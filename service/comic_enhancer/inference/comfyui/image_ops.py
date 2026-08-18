from __future__ import annotations

from io import BytesIO
from math import ceil, floor
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat


CHARACTER_CHROMA_GAIN = 1.8


# 方法说明：按统一格式原子保存推理结果图。
def save_output(image: Image.Image, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.webp")
    image.save(temporary, format="WEBP", quality=93, method=4)
    temporary.replace(output_path)


# 方法说明：将图像等比缩放并填充为正方形。
def pad_square(image_bytes: bytes, size: int = 512) -> bytes:
    output = BytesIO()
    with Image.open(BytesIO(image_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
        scale = min(size / source.width, size / source.height)
        resized = source.resize(
            (
                max(1, round(source.width * scale)),
                max(1, round(source.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(
        resized,
        ((size - resized.width) // 2, (size - resized.height) // 2),
    )
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


# 方法说明：恢复生成图与原图一致的宽高比例，并按指定倍率输出。
def restore_geometry(
    source_bytes: bytes,
    generated: Image.Image,
    output_scale: int = 1,
) -> Image.Image:
    if output_scale < 1:
        raise ValueError("output_scale must be at least 1")
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file)
        source_size = source.size
    source_width, source_height = source_size
    source_ratio = source_width / source_height
    generated_ratio = generated.width / generated.height
    if generated_ratio > source_ratio:
        content_width = min(
            generated.width,
            max(1, round(generated.height * source_ratio)),
        )
        left = (generated.width - content_width) // 2
        generated = generated.crop((left, 0, left + content_width, generated.height))
    elif generated_ratio < source_ratio:
        content_height = min(
            generated.height,
            max(1, round(generated.width / source_ratio)),
        )
        top = (generated.height - content_height) // 2
        generated = generated.crop((0, top, generated.width, top + content_height))
    output_size = (
        source_width * output_scale,
        source_height * output_scale,
    )
    return generated.resize(output_size, Image.Resampling.LANCZOS)


# 方法说明：将原图明度、文字和墨线回注到预设工作流结果。
def protect_source_structure(
    source_bytes: bytes,
    generated: Image.Image,
) -> Image.Image:
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
    source = source.resize(generated.size, Image.Resampling.LANCZOS)

    source_y, _, _ = source.convert("YCbCr").split()
    _, generated_cb, generated_cr = generated.convert("YCbCr").split()
    colorized = Image.merge(
        "YCbCr",
        (source_y, generated_cb, generated_cr),
    ).convert("RGB")

    color_mask = source_y.point(
        lambda value: max(0, min(255, round((245 - value) * 255 / 80)))
    )
    colorized = Image.composite(colorized, source, color_mask)

    dark_mask = source_y.point(
        lambda value: (
            255
            if value <= 112
            else max(0, min(255, round((176 - value) * 255 / 64)))
        )
    )
    return Image.composite(source, colorized, dark_mask)


# 方法说明：保留全页生成色度，并用原图明度和深色墨线锁定页面结构。
def protect_source_luminance_and_ink(
    source_bytes: bytes,
    generated: Image.Image,
    *,
    chroma_gain: float = CHARACTER_CHROMA_GAIN,
    chroma_blur_radius: float = 0.0,
) -> Image.Image:
    """保留原图明度和墨线，并按档位提取生成图色度。"""
    if chroma_gain <= 0:
        raise ValueError("chroma_gain must be positive")
    if chroma_blur_radius < 0:
        raise ValueError("chroma_blur_radius must not be negative")
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
    source = source.resize(generated.size, Image.Resampling.LANCZOS)

    source_y, _, _ = source.convert("YCbCr").split()
    _, generated_cb, generated_cr = generated.convert("YCbCr").split()
    if chroma_blur_radius:
        blur = ImageFilter.GaussianBlur(chroma_blur_radius)
        generated_cb = generated_cb.filter(blur)
        generated_cr = generated_cr.filter(blur)
    generated_cb = generated_cb.point(
        lambda value: max(
            0,
            min(255, round(128 + (value - 128) * chroma_gain)),
        )
    )
    generated_cr = generated_cr.point(
        lambda value: max(
            0,
            min(255, round(128 + (value - 128) * chroma_gain)),
        )
    )
    colorized = Image.merge(
        "YCbCr",
        (source_y, generated_cb, generated_cr),
    ).convert("RGB")

    dark_mask = source_y.point(
        lambda value: (
            255
            if value <= 112
            else max(0, min(255, round((176 - value) * 255 / 64)))
        )
    )
    return Image.composite(source, colorized, dark_mask)


# 方法说明：只回注原图深色墨线，保留生成图的明度和色彩层次。
def protect_source_ink_only(
    source_bytes: bytes,
    generated: Image.Image,
    *,
    chroma_gain: float = 1.0,
    chroma_blur_radius: float = 0.0,
) -> Image.Image:
    if chroma_gain <= 0:
        raise ValueError("chroma_gain must be positive")
    if chroma_blur_radius < 0:
        raise ValueError("chroma_blur_radius must not be negative")
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
    source = source.resize(generated.size, Image.Resampling.LANCZOS)
    generated = generated.convert("RGB")
    generated_y, generated_cb, generated_cr = generated.convert("YCbCr").split()
    if chroma_blur_radius:
        blur = ImageFilter.GaussianBlur(chroma_blur_radius)
        generated_cb = generated_cb.filter(blur)
        generated_cr = generated_cr.filter(blur)
    generated_cb = generated_cb.point(
        lambda value: max(
            0,
            min(255, round(128 + (value - 128) * chroma_gain)),
        )
    )
    generated_cr = generated_cr.point(
        lambda value: max(
            0,
            min(255, round(128 + (value - 128) * chroma_gain)),
        )
    )
    colorized = Image.merge(
        "YCbCr",
        (generated_y, generated_cb, generated_cr),
    ).convert("RGB")

    source_y = source.convert("YCbCr").getchannel("Y")
    dark_mask = source_y.point(
        lambda value: (
            255
            if value <= 40
            else max(0, min(255, round((96 - value) * 255 / 56)))
        )
    )
    return Image.composite(source, colorized, dark_mask)


# 方法说明：仅在 OCR 多边形内回贴原图文字笔画，避免覆盖框内生成背景。
def protect_source_text_regions(
    source_bytes: bytes,
    generated: Image.Image,
    regions: tuple[tuple[tuple[float, float], ...], ...],
    *,
    padding: int = 4,
    feather_radius: float = 1.5,
) -> Image.Image:
    if padding < 0:
        raise ValueError("padding must not be negative")
    if feather_radius < 0:
        raise ValueError("feather_radius must not be negative")
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
    source_width, source_height = source.size
    generated = generated.convert("RGB")
    if not regions:
        return generated

    scale_x = generated.width / source_width
    scale_y = generated.height / source_height
    source = source.resize(generated.size, Image.Resampling.LANCZOS)
    source_y = source.convert("YCbCr").getchannel("Y")
    mask = Image.new("L", generated.size, 0)
    for region in regions:
        if len(region) < 3:
            continue
        polygon = [
            (point_x * scale_x, point_y * scale_y)
            for point_x, point_y in region
        ]
        left = max(0, floor(min(point[0] for point in polygon)) - padding)
        top = max(0, floor(min(point[1] for point in polygon)) - padding)
        right = min(
            generated.width,
            ceil(max(point[0] for point in polygon)) + padding + 1,
        )
        bottom = min(
            generated.height,
            ceil(max(point[1] for point in polygon)) + padding + 1,
        )
        if right <= left or bottom <= top:
            continue
        region_box = (left, top, right, bottom)
        local_polygon = [
            (point_x - left, point_y - top)
            for point_x, point_y in polygon
        ]
        region_mask = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(region_mask).polygon(local_polygon, fill=255)
        if padding:
            region_mask = region_mask.filter(
                ImageFilter.MaxFilter(padding * 2 + 1)
            )
        source_region = source_y.crop(region_box)
        ink_mask = _text_ink_mask(source_region, region_mask)
        protected_region = ImageChops.multiply(region_mask, ink_mask)
        mask.paste(
            ImageChops.lighter(mask.crop(region_box), protected_region),
            (left, top),
        )
    if feather_radius:
        mask = mask.filter(ImageFilter.GaussianBlur(feather_radius))
    return Image.composite(source, generated, mask)


# 方法说明：按文字框背景明暗选择黑字或白字笔画蒙版。
def _text_ink_mask(
    source_luminance: Image.Image,
    region_mask: Image.Image,
) -> Image.Image:
    median = ImageStat.Stat(source_luminance, mask=region_mask).median[0]
    if median < 128:
        return source_luminance.point(
            lambda value: (
                255
                if value >= 192
                else max(0, min(255, round((value - 96) * 255 / 96)))
            )
        )
    return source_luminance.point(
        lambda value: (
            255
            if value <= 96
            else max(0, min(255, round((192 - value) * 255 / 96)))
        )
    )


# 方法说明：保留原图高频结构，并融合生成图低频明度和色彩层次。
def protect_source_high_frequency_structure(
    source_bytes: bytes,
    generated: Image.Image,
    *,
    chroma_gain: float = 1.0,
    chroma_blur_radius: float = 0.0,
    luminance_blend: float = 0.65,
    luminance_blur_radius: float = 8.0,
) -> Image.Image:
    if chroma_gain <= 0:
        raise ValueError("chroma_gain must be positive")
    if chroma_blur_radius < 0:
        raise ValueError("chroma_blur_radius must not be negative")
    if not 0 <= luminance_blend <= 1:
        raise ValueError("luminance_blend must be between zero and one")
    if luminance_blur_radius <= 0:
        raise ValueError("luminance_blur_radius must be positive")

    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
    source = source.resize(generated.size, Image.Resampling.LANCZOS)
    generated = generated.convert("RGB")
    source_y = source.convert("YCbCr").getchannel("Y")
    generated_y, generated_cb, generated_cr = generated.convert("YCbCr").split()

    chroma_blur = ImageFilter.GaussianBlur(chroma_blur_radius)
    if chroma_blur_radius:
        generated_cb = generated_cb.filter(chroma_blur)
        generated_cr = generated_cr.filter(chroma_blur)
    generated_cb = generated_cb.point(
        lambda value: max(
            0,
            min(255, round(128 + (value - 128) * chroma_gain)),
        )
    )
    generated_cr = generated_cr.point(
        lambda value: max(
            0,
            min(255, round(128 + (value - 128) * chroma_gain)),
        )
    )

    luminance_blur = ImageFilter.GaussianBlur(luminance_blur_radius)
    source_low = source_y.filter(luminance_blur)
    generated_low = generated_y.filter(luminance_blur)
    brighter = ImageChops.subtract(generated_low, source_low).point(
        lambda value: round(value * luminance_blend)
    )
    darker = ImageChops.subtract(source_low, generated_low).point(
        lambda value: round(value * luminance_blend)
    )
    blended_y = ImageChops.add(source_y, brighter)
    blended_y = ImageChops.subtract(blended_y, darker)
    colorized = Image.merge(
        "YCbCr",
        (blended_y, generated_cb, generated_cr),
    ).convert("RGB")

    dark_mask = source_y.point(
        lambda value: (
            255
            if value <= 40
            else max(0, min(255, round((96 - value) * 255 / 56)))
        )
    )
    return Image.composite(source, colorized, dark_mask)
