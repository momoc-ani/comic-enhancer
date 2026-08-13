from io import BytesIO

import pytest
from PIL import Image

from comic_enhancer.references import ReferenceImageStore


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
