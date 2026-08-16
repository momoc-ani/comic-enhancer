from io import BytesIO

from PIL import Image

from comic_enhancer.inference.comfyui.image_ops import (
    pad_square,
    protect_source_ink_only,
    protect_source_luminance_and_ink,
    protect_source_structure,
    restore_geometry,
)


# 方法说明：将测试图像编码为内存字节。
def image_bytes(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


# 方法说明：验证结构保护会恢复黑色文字和原图明度。
def test_structure_protection_restores_black_text_and_source_luminance():
    source = Image.new("RGB", (8, 8), "white")
    for y in range(2, 6):
        source.putpixel((3, y), (0, 0, 0))
    generated = Image.new("RGB", (16, 16), (240, 20, 20))

    result = protect_source_structure(
        image_bytes(source),
        generated,
    )

    text_pixel = result.getpixel((6, 6))
    white_pixel = result.getpixel((0, 0))
    assert max(text_pixel) <= 16
    assert min(white_pixel) >= 245


# 方法说明：验证角色档结构保护会锁定墨线，同时保留浅色背景的生成色度。
def test_luminance_and_ink_protection_keeps_full_page_chroma():
    source = Image.new("RGB", (8, 8), "white")
    for y in range(2, 6):
        source.putpixel((3, y), (0, 0, 0))
    generated = Image.new("RGB", (16, 16), (80, 120, 200))

    result = protect_source_luminance_and_ink(
        image_bytes(source),
        generated,
    )

    ink_pixel = result.getpixel((6, 6))
    background_pixel = result.getpixel((0, 0))
    assert max(ink_pixel) <= 16
    assert background_pixel[2] >= background_pixel[0] + 35


# 方法说明：验证线稿档只锁定原图深色墨线并保留生成图明度。
def test_ink_only_protection_keeps_generated_bright_background():
    source = Image.new("RGB", (8, 8), (80, 80, 80))
    for y in range(2, 6):
        source.putpixel((3, y), (0, 0, 0))
    generated = Image.new("RGB", (8, 8), (60, 150, 240))

    result = protect_source_ink_only(
        image_bytes(source),
        generated,
        chroma_gain=1.25,
        chroma_blur_radius=1.0,
    )

    ink_pixel = result.getpixel((3, 3))
    background_pixel = result.getpixel((0, 0))
    assert max(ink_pixel) <= 16
    assert background_pixel[2] > background_pixel[0]
    assert background_pixel[2] > 180


# 方法说明：验证竖图几何恢复后保持原始比例。
def test_geometry_round_trip_preserves_portrait_ratio():
    source = Image.new("RGB", (100, 200), "red")

    padded_bytes = pad_square(image_bytes(source))
    with Image.open(BytesIO(padded_bytes)) as padded:
        assert padded.size == (512, 512)
        assert padded.getpixel((0, 256)) == (255, 255, 255)
        assert padded.getpixel((256, 256))[0] >= 250

    generated = Image.new("RGB", (1024, 1024), "blue")
    restored = restore_geometry(image_bytes(source), generated)
    assert restored.size == source.size
    assert restored.getpixel((50, 100))[2] >= 250


# 方法说明：验证横图几何恢复后保持原始比例。
def test_geometry_round_trip_preserves_landscape_ratio():
    source = Image.new("RGB", (200, 100), "red")
    generated = Image.new("RGB", (1024, 1024), "blue")

    restored = restore_geometry(image_bytes(source), generated)

    assert restored.size == source.size


# 方法说明：验证竖图按生成图高度居中裁剪后恢复原始比例。
def test_restore_geometry_uses_generated_height_for_portrait_crop():
    source = Image.new("RGB", (100, 200), "white")
    generated = Image.new("RGB", (140, 200), "white")
    for x in range(generated.width):
        for y in range(generated.height):
            generated.putpixel((x, y), (x, 0, 0))

    restored = restore_geometry(image_bytes(source), generated)

    assert restored.size == source.size
    assert restored.getpixel((0, 100))[0] < 25
    assert restored.getpixel((99, 100))[0] > 115


# 方法说明：验证横图按生成图宽度居中裁剪后恢复原始比例。
def test_restore_geometry_uses_generated_width_for_landscape_crop():
    source = Image.new("RGB", (200, 100), "white")
    generated = Image.new("RGB", (200, 140), "white")
    for y in range(generated.height):
        for x in range(generated.width):
            generated.putpixel((x, y), (0, y, 0))

    restored = restore_geometry(image_bytes(source), generated)

    assert restored.size == source.size
    assert restored.getpixel((100, 0))[1] < 25
    assert restored.getpixel((100, 99))[1] > 115


# 方法说明：验证几何恢复可精确输出原图两倍尺寸且不改变比例。
def test_restore_geometry_can_return_exact_two_x_size_without_ratio_drift():
    source = Image.new("RGB", (1124, 1600), "white")
    generated = Image.new("RGB", (784, 1120), "blue")

    restored = restore_geometry(
        image_bytes(source),
        generated,
        output_scale=2,
    )

    assert restored.size == (2248, 3200)
    assert restored.width / restored.height == source.width / source.height
