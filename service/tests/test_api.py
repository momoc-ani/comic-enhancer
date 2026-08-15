from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.config import Settings
from comic_enhancer.config import load_settings
from comic_enhancer.main import (
    create_app,
    prioritized_metadata_candidates,
)
from comic_enhancer.models import (
    MetadataResolution,
    ProcessOptions,
    ProcessingMode,
    WorkIdentity,
    WorkMetadata,
)


# 方法说明：生成测试使用的 PNG 图片字节。
def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (32, 48), "white").save(stream, format="PNG")
    return stream.getvalue()


# 方法说明：验证页面处理会使用通用适配器并复用缓存。
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


# 方法说明：验证能力接口要求有效鉴权。
def test_capabilities_require_auth(tmp_path):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
    )
    client = TestClient(create_app(settings))

    assert client.get("/v1/capabilities").status_code == 401


# 方法说明：验证 FLUX.2 是合法的处理档位。
def test_flux2_mode_is_valid():
    options = ProcessOptions(mode="flux2")

    assert options.mode == ProcessingMode.FLUX2


# 方法说明：验证 FLUX.2 仅在候选工作流就绪时对外声明。
def test_capabilities_advertise_flux2_only_when_candidate_is_ready(tmp_path, monkeypatch):
    flux2_workflow = tmp_path / "flux2.json"
    flux2_workflow.write_text("{}", encoding="utf-8")
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        backend="comfyui",
        comfyui_flux2_enabled=True,
        comfyui_workflow_flux2=flux2_workflow,
    )
    app = create_app(settings)
    monkeypatch.setattr(app.state.processor.backend, "ready", lambda: True)
    monkeypatch.setattr(app.state.processor.backend, "flux2_profile_ready", lambda: True)
    client = TestClient(app)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "flux2" in payload["processing_modes"]
    assert payload["flux2_available"] is True


# 方法说明：验证元数据解析需要鉴权并返回缓存结构。
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


# 方法说明：验证后端版本变化会生成不同缓存键。
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


# 方法说明：验证 JSON 配置中的路径字段会转换为路径对象。
def test_json_config_converts_path_fields(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    config.write_text(
        '{"runtime_dir":"runtime","adapter_index":"index.json",'
        '"adapter_weights_root":"weights",'
        '"realcugan_resource_root":"resource/realcugan",'
        '"cobra_url":"http://127.0.0.1:8780",'
        '"comfyui_cobra_url":"http://127.0.0.1:8192",'
        '"comfyui_workflow_fast":"fast.json",'
        '"comfyui_workflow_quality":"quality.json",'
        '"comfyui_workflow_flux2":"flux2.json",'
        '"work_identity_index":"identities.json",'
        '"comfyui_workflow_root":"workflows"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("COMIC_ENHANCER_CONFIG", str(config))

    settings = load_settings()

    assert settings.runtime_dir == Path("runtime")
    assert settings.adapter_index == Path("index.json")
    assert settings.comfyui_workflow_fast == Path("fast.json")
    assert settings.comfyui_workflow_flux2 == Path("flux2.json")
    assert settings.realcugan_resource_root == Path("resource/realcugan")
    assert settings.realcugan_enabled is False
    assert settings.flux2_reference_limit == 3
    assert settings.work_identity_index == Path("identities.json")
    assert settings.comfyui_workflow_root == Path("workflows")


# 方法说明：验证外部 ID 精确匹配的元数据具有角色优先级。
def test_exact_external_id_metadata_candidate_has_character_priority():
    work = WorkIdentity(
        source="copy_manga",
        source_work_id="heavy-knight",
        title="被追放的轉生重騎士用遊戲知識開無雙",
        external_ids={"anilist": "150193"},
    )
    resolution = MetadataResolution(
        work_key=work.key,
        title=work.title,
        candidates=[
            WorkMetadata(
                provider="bangumi",
                provider_id="other",
                title=work.title,
                confidence=1.0,
            ),
            WorkMetadata(
                provider="anilist",
                provider_id="150193",
                title="追放された転生重騎士はゲーム知識で無双する",
                confidence=0.8,
            ),
        ],
    )

    ordered = prioritized_metadata_candidates(resolution, work)

    assert ordered[0].provider == "anilist"
    assert ordered[0].provider_id == "150193"
