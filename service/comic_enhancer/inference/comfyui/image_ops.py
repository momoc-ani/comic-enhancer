from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


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


# 方法说明：回注原图结构并保留 Cobra 生成的高光色彩。
def protect_cobra_structure(
    source_bytes: bytes,
    generated: Image.Image,
) -> Image.Image:
    with Image.open(BytesIO(source_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file).convert("RGB")
    source = source.resize(generated.size, Image.Resampling.LANCZOS)
    source_y, _, _ = source.convert("YCbCr").split()
    generated = generated.convert("RGB")
    generated_y, generated_cb, generated_cr = generated.convert("YCbCr").split()
    _, generated_saturation, _ = generated.convert("HSV").split()
    source_highlight_mask = source_y.point(
        lambda value: 255 if value >= 248 else max(0, round((value - 220) * 255 / 28))
    )
    generated_color_mask = generated_saturation.point(
        lambda value: 255 if value >= 56 else max(0, round((value - 20) * 255 / 36))
    )
    generated_color_luma_mask = generated_y.point(
        lambda value: 255 if value >= 160 else max(0, round((value - 96) * 255 / 64))
    )
    colored_highlight_mask = ImageChops.multiply(
        source_highlight_mask,
        ImageChops.multiply(generated_color_mask, generated_color_luma_mask),
    )
    highlight_y = ImageChops.darker(
        source_y,
        ImageChops.lighter(generated_y, Image.new("L", generated.size, 192)),
    )
    protected_y = Image.composite(highlight_y, source_y, colored_highlight_mask)
    colorized = Image.merge(
        "YCbCr",
        (protected_y, generated_cb, generated_cr),
    ).convert("RGB")
    ink_mask = source_y.point(
        lambda value: (
            255
            if value <= 52
            else max(0, min(180, round((84 - value) * 180 / 32)))
        )
    )
    generated_light_mask = generated_y.point(
        lambda value: 255 if value >= 244 else max(0, round((value - 224) * 255 / 20))
    )
    generated_neutral_mask = generated_saturation.point(
        lambda value: 255 if value <= 16 else max(0, round((48 - value) * 255 / 32))
    )
    paper_mask = ImageChops.multiply(
        source_highlight_mask,
        ImageChops.multiply(generated_light_mask, generated_neutral_mask),
    )
    structure_mask = ImageChops.lighter(ink_mask, paper_mask)
    return Image.composite(source, colorized, structure_mask)
