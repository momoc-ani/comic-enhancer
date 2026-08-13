import pytest

from analysis_service.masks import encode_binary_mask
from comic_enhancer.backends import ComfyUIBackend
from comic_enhancer.models import (
    BoundingBox,
    CharacterInstance,
    CharacterMask,
    PanelRegion,
)


def test_binary_mask_rle_round_trip():
    source = [
        [0, 0, 1, 1],
        [0, 1, 1, 0],
        [0, 0, 0, 0],
    ]
    encoded = CharacterMask(
        width=4,
        height=3,
        counts=encode_binary_mask(source),
        score=0.95,
    )

    decoded = ComfyUIBackend._decode_character_mask(encoded)

    assert list(decoded.getdata()) == [
        0, 0, 255, 255,
        0, 255, 255, 0,
        0, 0, 0, 0,
    ]


def test_character_mask_rejects_invalid_run_total():
    with pytest.raises(ValueError, match="dimensions"):
        CharacterMask(width=4, height=3, counts=[12, 1], score=0.9)


def test_character_mask_is_positioned_without_rescaling():
    mask = CharacterMask(width=2, height=2, counts=[1, 2, 1], score=0.9)
    character = CharacterInstance(
        instance_id="one",
        cluster_id="cluster",
        box=BoundingBox(x1=12, y1=23, x2=14, y2=25),
        mask=mask,
    )
    panel = PanelRegion(
        panel_index=0,
        box=BoundingBox(x1=10, y1=20, x2=20, y2=30),
    )

    positioned = ComfyUIBackend._panel_character_mask([character], panel)

    assert positioned.size == (10, 10)
    assert positioned.getpixel((3, 3)) > 64
    assert positioned.getpixel((8, 8)) == 0
