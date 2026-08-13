from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.config import Settings
from comic_enhancer.config import load_settings
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
    assert first.json()["reference_applied"] is False
    assert first.json()["model_profile"] == "passthrough"
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


def test_metadata_resolve_requires_auth_and_returns_cached_shape(tmp_path):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        metadata_enabled=False,
    )
    client = TestClient(create_app(settings))
    payload = {"work_json": '{"source":"copy_manga","source_work_id":"123","title":"测试作品"}'}

    assert client.post("/v1/metadata/resolve", data=payload).status_code == 401
    response = client.post(
        "/v1/metadata/resolve",
        headers={"Authorization": "Bearer test-token"},
        data=payload,
    )
    assert response.status_code == 200
    assert response.json()["work_key"] == "copy_manga:123"
    assert response.json()["selected"] is None


def test_cache_key_changes_with_backend_revision(tmp_path):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
    )
    app = create_app(settings)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    data = {
        "work_json": '{"source":"copy_manga","source_work_id":"123"}',
        "options_json": '{"mode":"fast"}',
    }

    first = client.post(
        "/v1/pages/process",
        headers=headers,
        data=data,
        files={"image": ("page.png", png_bytes(), "image/png")},
    )
    app.state.processor.backend.name = "passthrough-v2"
    second = client.post(
        "/v1/pages/process",
        headers=headers,
        data=data,
        files={"image": ("page.png", png_bytes(), "image/png")},
    )

    assert first.json()["cache_key"] != second.json()["cache_key"]
    assert second.json()["cached"] is False


def test_json_config_converts_path_fields(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    config.write_text(
        '{"runtime_dir":"runtime","adapter_index":"index.json",'
        '"adapter_weights_root":"weights",'
        '"comfyui_reference_enabled":true,'
        '"comfyui_workflow_fast":"fast.json",'
        '"comfyui_workflow_quality":"quality.json",'
        '"comfyui_workflow_reference_quality":"reference.json",'
        '"comfyui_reference_ready_file":"models/ready",'
        '"comfyui_workflow_root":"workflows"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("COMIC_ENHANCER_CONFIG", str(config))

    settings = load_settings()

    assert settings.runtime_dir == Path("runtime")
    assert settings.adapter_index == Path("index.json")
    assert settings.comfyui_reference_enabled is True
    assert settings.comfyui_workflow_fast == Path("fast.json")
    assert settings.comfyui_workflow_reference_quality == Path("reference.json")
    assert settings.comfyui_reference_ready_file == Path("models/ready")
    assert settings.comfyui_workflow_root == Path("workflows")
