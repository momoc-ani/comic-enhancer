from io import BytesIO

from PIL import Image

from comic_enhancer.backends import ComfyUIBackend


def image_bytes(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_structure_protection_restores_black_text_and_source_luminance():
    source = Image.new("RGB", (8, 8), "white")
    for y in range(2, 6):
        source.putpixel((3, y), (0, 0, 0))
    generated = Image.new("RGB", (16, 16), (240, 20, 20))

    result = ComfyUIBackend._protect_source_structure(
        image_bytes(source),
        generated,
    )

    text_pixel = result.getpixel((6, 6))
    white_pixel = result.getpixel((0, 0))
    assert max(text_pixel) <= 16
    assert min(white_pixel) >= 245


def test_cobra_structure_preserves_dark_text_without_flattening_color():
    source = Image.new("RGB", (8, 8), (210, 210, 210))
    for y in range(2, 6):
        source.putpixel((3, y), (0, 0, 0))
    generated = Image.new("RGB", (16, 16), (230, 70, 120))

    result = ComfyUIBackend._protect_cobra_structure(
        image_bytes(source),
        generated,
    )

    text_pixel = result.getpixel((6, 6))
    colored = result.getpixel((0, 0))
    assert max(text_pixel) <= 16
    assert colored[0] > colored[2] - 80
    assert max(colored) - min(colored) > 50


def test_cobra_structure_does_not_treat_colored_white_regions_as_paper():
    source = Image.new("RGB", (8, 8), "white")
    generated = Image.new("RGB", (8, 8), (80, 150, 240))

    result = ComfyUIBackend._protect_cobra_structure(
        image_bytes(source),
        generated,
    )

    colored = result.getpixel((4, 4))
    assert colored[2] > 220
    assert colored[2] > colored[0] + 60


def test_cobra_structure_restores_neutral_white_paper():
    source = Image.new("RGB", (8, 8), "white")
    generated = Image.new("RGB", (8, 8), (242, 240, 238))

    result = ComfyUIBackend._protect_cobra_structure(
        image_bytes(source),
        generated,
    )

    assert min(result.getpixel((4, 4))) >= 250


def test_cobra_structure_removes_generated_neutral_text_ghosts():
    source = Image.new("RGB", (8, 8), "white")
    generated = Image.new("RGB", (8, 8), "white")
    generated.putpixel((4, 4), (0, 0, 0))

    result = ComfyUIBackend._protect_cobra_structure(
        image_bytes(source),
        generated,
    )

    assert min(result.getpixel((4, 4))) >= 250


def test_flux2_structure_keeps_generated_color_and_restores_black_ink():
    source = Image.new("RGB", (8, 8), (180, 180, 180))
    source.putpixel((3, 3), (0, 0, 0))
    generated = Image.new("RGB", (8, 8), (35, 120, 240))

    result = ComfyUIBackend._protect_flux2_structure(
        image_bytes(source),
        generated,
    )

    assert max(result.getpixel((3, 3))) <= 16
    colored = result.getpixel((0, 0))
    assert colored[2] >= 235
    assert colored[2] > colored[0] + 100


def test_flux2_structure_rejects_new_marks_on_source_paper():
    source = Image.new("RGB", (8, 8), "white")
    generated = Image.new("RGB", (8, 8), (35, 120, 240))
    generated.putpixel((4, 4), (0, 0, 0))

    result = ComfyUIBackend._protect_flux2_structure(
        image_bytes(source),
        generated,
    )

    assert min(result.getpixel((4, 4))) >= 245


def test_geometry_round_trip_preserves_portrait_ratio():
    source = Image.new("RGB", (100, 200), "red")

    padded_bytes = ComfyUIBackend._pad_square(image_bytes(source))
    with Image.open(BytesIO(padded_bytes)) as padded:
        assert padded.size == (512, 512)
        assert padded.getpixel((0, 256)) == (255, 255, 255)
        assert padded.getpixel((256, 256))[0] >= 250

    generated = Image.new("RGB", (1024, 1024), "blue")
    restored = ComfyUIBackend._restore_geometry(image_bytes(source), generated)
    assert restored.size == source.size
    assert restored.getpixel((50, 100))[2] >= 250


def test_geometry_round_trip_preserves_landscape_ratio():
    source = Image.new("RGB", (200, 100), "red")
    generated = Image.new("RGB", (1024, 1024), "blue")

    restored = ComfyUIBackend._restore_geometry(image_bytes(source), generated)

    assert restored.size == source.size
