from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.config import Settings
from comic_enhancer.config import load_settings
from comic_enhancer.main import (
    create_app,
    effective_analyzer_profile,
    prioritized_metadata_candidates,
)
from comic_enhancer.models import (
    ChapterAnalysisResult,
    CharacterReference,
    MetadataResolution,
    ProcessOptions,
    ProcessingMode,
    WorkIdentity,
    WorkMetadata,
)


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (32, 48), "white").save(stream, format="PNG")
    return stream.getvalue()


def reference_bytes(size, color, *, grayscale=False, full_body=False):
    stream = BytesIO()
    mode = "L" if grayscale else "RGB"
    image = Image.new(mode, size, 235 if grayscale else color)
    if full_body:
        fill = 40 if grayscale else (30, 80, 140)
        image = Image.new(mode, size, 255 if grayscale else "white")
        image.paste(
            fill,
            (
                round(size[0] * 0.30),
                round(size[1] * 0.03),
                round(size[0] * 0.70),
                round(size[1] * 0.97),
            ),
        )
    else:
        stripe_width = max(1, size[0] // 12)
        fill = 40 if grayscale else (30, 80, 140)
        for x in range(0, size[0], stripe_width * 2):
            image.paste(fill, (x, 0, min(size[0], x + stripe_width), size[1]))
    image.save(stream, format="PNG")
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


def test_manganinja_mode_is_valid():
    options = ProcessOptions(mode="manganinja")

    assert options.mode == ProcessingMode.MANGANINJA


def test_effective_analyzer_profile_tracks_reference_selection_revision():
    assert effective_analyzer_profile(None) is None
    assert effective_analyzer_profile("magiv2@test") == (
        "magiv2@test+reference-view-v2"
    )


def test_capabilities_declare_processing_modes_and_manganinja_state(
    tmp_path, monkeypatch
):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        comfyui_reference_enabled=True,
        analyzer_enabled=True,
    )
    app = create_app(settings)
    monkeypatch.setattr(app.state.processor.backend, "reference_profile_ready", lambda: True)
    monkeypatch.setattr(app.state.analyzer, "ready", lambda: True)
    client = TestClient(app)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_modes"] == ["fast", "quality", "manganinja"]
    assert payload["manganinja_available"] is True
    assert payload["cobra_available"] is False


def test_capabilities_advertise_cobra_only_when_candidate_is_ready(tmp_path, monkeypatch):
    cobra_workflow = tmp_path / "cobra.json"
    cobra_workflow.write_text("{}", encoding="utf-8")
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        backend="comfyui",
        comfyui_cobra_enabled=True,
        comfyui_workflow_cobra=cobra_workflow,
    )
    app = create_app(settings)
    monkeypatch.setattr(app.state.processor.backend, "ready", lambda: True)
    monkeypatch.setattr(app.state.processor.backend, "cobra_profile_ready", lambda: True)
    client = TestClient(app)

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "cobra" in payload["processing_modes"]
    assert payload["cobra_available"] is True


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
        '"analyzer_url":"http://127.0.0.1:8770",'
        '"cobra_url":"http://127.0.0.1:8780",'
        '"comfyui_reference_url":"http://127.0.0.1:8191",'
        '"comfyui_cobra_url":"http://127.0.0.1:8192",'
        '"comfyui_reference_enabled":true,'
        '"comfyui_workflow_fast":"fast.json",'
        '"comfyui_workflow_quality":"quality.json",'
        '"comfyui_workflow_reference_quality":"reference.json",'
        '"comfyui_reference_ready_file":"models/ready",'
        '"work_identity_index":"identities.json",'
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
    assert settings.comfyui_cobra_enabled is False
    assert settings.cobra_reference_limit == 12
    assert settings.comfyui_reference_ready_file == Path("models/ready")
    assert settings.work_identity_index == Path("identities.json")
    assert settings.comfyui_workflow_root == Path("workflows")


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


def test_analyze_deduplicates_characters_and_selects_best_color_reference(
    tmp_path,
    monkeypatch,
):
    identity_index = tmp_path / "identities.json"
    identity_index.write_text(
        """{
          "works": [{
            "identity_id": "heavy-knight",
            "title_aliases": ["被追放的轉生重騎士用遊戲知識開無雙"],
            "external_ids": {"bangumi": "418302", "anilist": "150193"},
            "characters": [
              {"identity_id": "elymas", "name": "Elymas Edvan", "aliases": ["エルマ・エドヴァン"], "external_ids": {"bangumi": "173007", "anilist": "277688"}},
              {"identity_id": "luce", "name": "Luce Rubis", "aliases": ["ルーチェ・ルービス"], "external_ids": {"bangumi": "173008", "anilist": "344248"}},
              {"identity_id": "maris", "name": "Maris Edvan", "aliases": ["マリス・エドヴァン"], "external_ids": {"bangumi": "173009", "anilist": "342589"}}
            ]
          }]
        }""",
        encoding="utf-8",
    )
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        analyzer_enabled=True,
        work_identity_index=identity_index,
    )
    app = create_app(settings)
    work = WorkIdentity(
        source="copy_manga",
        source_work_id="heavy-knight",
        title="被追放的轉生重騎士用遊戲知識開無雙",
        external_ids={"bangumi": "418302", "anilist": "150193"},
    )
    urls = {
        "bgm-elymas": reference_bytes(
            (1000, 1400), (120, 70, 50), full_body=True
        ),
        "ani-elymas": reference_bytes((230, 345), (220, 60, 90)),
        "bgm-luce": reference_bytes(
            (690, 1050), (100, 80, 180), full_body=True
        ),
        "ani-luce": reference_bytes((230, 345), (230, 30, 100)),
        "bgm-maris": reference_bytes(
            (700, 1170), (80, 130, 180), full_body=True
        ),
        "ani-maris": reference_bytes((230, 345), 0, grayscale=True),
        "ani-ares": reference_bytes((230, 345), 0, grayscale=True),
    }

    def character(provider, provider_id, name, url):
        return CharacterReference(
            provider=provider,
            provider_id=provider_id,
            name=name,
            image_url=url,
        )

    resolution = MetadataResolution(
        work_key=work.key,
        title=work.title,
        candidates=[
            WorkMetadata(
                provider="bangumi",
                provider_id="418302",
                title=work.title,
                confidence=1.0,
                characters=[
                    character("bangumi", "173007", "エルマ・エドヴァン", "bgm-elymas"),
                    character("bangumi", "173008", "ルーチェ・ルービス", "bgm-luce"),
                    character("bangumi", "173009", "マリス・エドヴァン", "bgm-maris"),
                ],
            ),
            WorkMetadata(
                provider="anilist",
                provider_id="150193",
                title=work.title,
                confidence=1.0,
                characters=[
                    character("anilist", "277688", "Elymas Edvan", "ani-elymas"),
                    character("anilist", "344248", "Luce Rubis", "ani-luce"),
                    character("anilist", "342589", "Maris Edvan", "ani-maris"),
                    character("anilist", "342590", "Ares", "ani-ares"),
                ],
            ),
        ],
    )
    monkeypatch.setattr(app.state.metadata, "resolve", lambda _: resolution)
    monkeypatch.setattr(app.state.references, "get", lambda url: urls[url])
    captured = {}

    def analyze(_, character_bank):
        captured["entries"] = [entry for entry, _ in character_bank]
        return ChapterAnalysisResult(analyzer_profile="test")

    monkeypatch.setattr(app.state.analyzer, "analyze", analyze)
    client = TestClient(app)
    response = client.post(
        "/v1/pages/analyze",
        headers={"Authorization": "Bearer test-token"},
        data={"work_json": work.model_dump_json()},
        files={"pages": ("page.png", png_bytes(), "image/png")},
    )

    assert response.status_code == 200
    entries = captured["entries"]
    assert len(entries) == 6
    assert {entry.name for entry in entries} == {
        "Elymas Edvan",
        "Luce Rubis",
        "Maris Edvan",
    }
    assert {
        entry.provider for entry in entries if entry.name == "Elymas Edvan"
    } == {"bangumi", "anilist"}
    assert {
        entry.image_url for entry in entries if entry.name == "Elymas Edvan"
    } == {"bgm-elymas"}
    assert {
        entry.image_url for entry in entries if entry.name == "Luce Rubis"
    } == {"bgm-luce"}
    assert {
        entry.image_url for entry in entries if entry.name == "Maris Edvan"
    } == {"bgm-maris"}
    assert {
        entry.character_id for entry in entries if entry.name == "Elymas Edvan"
    } == {"work:heavy-knight:elymas"}
    assert {
        entry.portrait_reference_url
        for entry in entries
        if entry.name == "Elymas Edvan"
    } == {"ani-elymas"}
    assert {
        entry.full_body_reference_url
        for entry in entries
        if entry.name == "Elymas Edvan"
    } == {"bgm-elymas"}
    assert [entry.name for entry in entries[:3]] == [
        "Elymas Edvan",
        "Luce Rubis",
        "Maris Edvan",
    ]
