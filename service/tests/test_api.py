from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.config import Settings
from comic_enhancer.main import create_app


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (32, 48), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_process_uses_generic_adapter_and_cache(tmp_path):
    (tmp_path / "generic.safetensors").write_bytes(b"generic")
    adapter_index = tmp_path / "index.json"
    adapter_index.write_text(
        """{
          "schema_version": 1,
          "generic": {
            "adapter_id": "generic-anime-v1",
            "name": "Generic Anime",
            "base_model": "sd15-anime",
            "revision": "v1",
            "file": "generic.safetensors"
          },
          "works": {}
        }""",
        encoding="utf-8",
    )
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=adapter_index,
        adapter_weights_root=tmp_path,
    )
    client = TestClient(create_app(settings))
    headers = {"Authorization": "Bearer test-token"}
    data = {
        "work_json": '{"source":"copy_manga","source_work_id":"123"}',
        "options_json": '{"mode":"fast","page_index":1}',
    }

    first = client.post(
        "/v1/pages/process",
        headers=headers,
        data=data,
        files={"image": ("page.png", png_bytes(), "image/png")},
    )
    second = client.post(
        "/v1/pages/process",
        headers=headers,
        data=data,
        files={"image": ("page.png", png_bytes(), "image/png")},
    )

    assert first.status_code == 200
    assert first.json()["adapter_source"] == "generic"
    assert first.json()["adapter_applied"] is False
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["adapter_applied"] is False

    result_path = first.json()["result_url"]
    assert client.get(result_path).status_code == 401
    image = client.get(result_path, headers=headers)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"


def test_capabilities_require_auth(tmp_path):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
    )
    client = TestClient(create_app(settings))

    assert client.get("/v1/capabilities").status_code == 401
