import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from comic_enhancer.backends import (
    REFERENCE_PROCESSING_REVISION,
    ComfyUIBackend,
    InferenceAssets,
)
from comic_enhancer.models import (
    AdapterManifest,
    AdapterSource,
    BoundingBox,
    CharacterInstance,
    CharacterMask,
    CharacterMatch,
    PageAnalysis,
    PanelRegion,
    ProcessOptions,
    ResolvedAdapter,
)
from comic_enhancer.workflows import PresetWorkflowLoader


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write_workflow(path, *, marker, load_nodes=1, save_nodes=1):
    workflow = {
        str(index): {
            "class_type": "LoadImage",
            "inputs": {"image": f"preset-{marker}.png"},
        }
        for index in range(1, load_nodes + 1)
    }
    workflow["marker"] = {"class_type": "TestNode", "inputs": {"value": marker}}
    for index in range(10, 10 + save_nodes):
        workflow[str(index)] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["marker", 0], "filename_prefix": "preset"},
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow), encoding="utf-8")


def resolved(adapter=None):
    return ResolvedAdapter(
        source=AdapterSource.WORK if adapter else AdapterSource.NONE,
        adapter=adapter,
        reason="test",
    )


def reference_assets():
    return InferenceAssets(
        image_bytes=b"page",
        analysis=PageAnalysis(
            image_hash="0" * 64,
            width=100,
            height=100,
            analyzer_profile="test",
            panels=[
                PanelRegion(
                    panel_index=0,
                    box=BoundingBox(x1=0, y1=0, x2=100, y2=100),
                    character_instance_ids=["character-1"],
                )
            ],
            characters=[
                CharacterInstance(
                    instance_id="character-1",
                    cluster_id="cluster-1",
                    box=BoundingBox(x1=10, y1=10, x2=50, y2=90),
                    panel_index=0,
                    match=CharacterMatch(
                        character_id="bangumi:1",
                        character_name="角色一",
                        reference_url="https://example.com/1.png",
                        status="accepted",
                        confidence=0.9,
                    ),
                    mask=CharacterMask(
                        width=40,
                        height=80,
                        counts=[0, 3200],
                        score=0.95,
                    ),
                )
            ],
        ),
        character_references={"bangumi:1": b"reference"},
    )


def png_bytes(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_cobra_backend_posts_multiple_references_and_restores_geometry(
    tmp_path, monkeypatch
):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    cobra = tmp_path / "cobra.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    cobra_prompt = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "page.png"},
            "_meta": {"title": "INPUT_IMAGE"},
        }
    }
    for index in range(1, 13):
        cobra_prompt[str(index + 1)] = {
            "class_type": "LoadImage",
            "inputs": {"image": f"ref-{index}.png"},
            "_meta": {"title": f"REFERENCE_IMAGE_{index}"},
        }
    cobra_prompt["14"] = {
        "class_type": "CobraColorize",
        "inputs": {
            "image": ["1", 0],
            **{
                f"reference_{index}": [str(index + 1), 0]
                for index in range(1, 13)
            },
            "reference_count": 3,
        },
    }
    cobra_prompt["15"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["14", 0]},
    }
    cobra.write_text(json.dumps(cobra_prompt), encoding="utf-8")
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=PresetWorkflowLoader(
            fast_workflow=fast,
            quality_workflow=quality,
            workflow_root=tmp_path,
            cobra_workflow=cobra,
        ),
        cobra_enabled=True,
        cobra_workflow=cobra,
        cobra_reference_limit=12,
    )
    monkeypatch.setattr(backend, "cobra_profile_ready", lambda: True)
    generated = png_bytes(Image.new("RGB", (16, 24), (230, 70, 120)))
    captured = {}

    class FakeResponse:
        content = generated

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *, base_url, timeout):
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, path, **kwargs):
            captured["path"] = path
            if path == "/upload/image":
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"name": "uploaded.png", "subfolder": ""},
                )
            captured["files"] = kwargs.get("files")
            captured["prompt"] = kwargs["json"]["prompt"]
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"prompt_id": "cobra-prompt"},
            )

        def get(self, path, **kwargs):
            if path.startswith("/history/"):
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "cobra-prompt": {
                            "status": {"status_str": "success"},
                            "outputs": {
                                    "15": {
                                    "images": [{"filename": "cobra.png", "subfolder": "", "type": "output"}]
                                }
                            },
                        }
                    },
                )
            return SimpleNamespace(
                raise_for_status=lambda: None,
                content=generated,
            )

    monkeypatch.setattr("comic_enhancer.backends.httpx.Client", FakeClient)
    source = Image.new("RGB", (8, 12), "white")
    assets = InferenceAssets(
        image_bytes=png_bytes(source),
        character_references={
            "character-a": png_bytes(Image.new("RGB", (4, 6), "red")),
            "character-b": png_bytes(Image.new("RGB", (4, 6), "blue")),
        },
    )
    output_path = tmp_path / "cobra.webp"

    outcome = backend.process(
        assets,
        output_path,
        ProcessOptions(mode="cobra"),
        resolved(),
    )

    assert outcome.model_profile == "cobra"
    assert outcome.reference_applied is True
    assert captured["base_url"] == "http://comfy"
    assert captured["path"] == "/prompt"
    assert captured["prompt"]["14"]["inputs"]["reference_1"] == ["2", 0]
    assert captured["prompt"]["14"]["inputs"]["reference_12"] == ["13", 0]
    assert captured["prompt"]["14"]["inputs"]["reference_count"] == 2
    with Image.open(output_path) as result:
        assert result.size == source.size


def test_loader_selects_mode_and_complete_adapter_workflow(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    adapter_workflow = tmp_path / "works" / "42-fast.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(adapter_workflow, marker="adapter")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        workflow_root=tmp_path,
    )
    adapter = AdapterManifest(
        adapter_id="work-42",
        name="Work 42",
        base_model="sd15-anime",
        revision="v1",
        file="work.safetensors",
        workflows={"fast": "works/42-fast.json"},
    )

    loaded = loader.load(ProcessOptions(mode="fast"), resolved(adapter))
    fallback = loader.load(ProcessOptions(mode="quality"), resolved(adapter))

    assert loaded.prompt["marker"]["inputs"]["value"] == "adapter"
    assert loaded.adapter_applied is True
    assert fallback.prompt["marker"]["inputs"]["value"] == "quality"
    assert fallback.adapter_applied is False
    assert loader.revision(ProcessOptions(mode="fast"), resolved(adapter)) != loader.revision(
        ProcessOptions(mode="quality"), resolved(adapter)
    )


def test_bind_io_discovers_nodes_and_rejects_placeholders():
    workflow = {
        "5": {"class_type": "LoadImage", "inputs": {"image": "preset.png"}},
        "18": {
            "class_type": "SaveImage",
            "inputs": {"images": ["5", 0], "filename_prefix": "preset"},
        },
    }

    output_nodes = ComfyUIBackend._bind_io(
        workflow,
        input_images={"INPUT_IMAGE": "uploaded/input.png"},
        output_prefix="comic-enhancer/job",
    )

    assert workflow["5"]["inputs"]["image"] == "uploaded/input.png"
    assert workflow["18"]["inputs"]["filename_prefix"] == "comic-enhancer/job"
    assert output_nodes == ("18",)

    workflow["5"]["inputs"]["model"] = "${MODEL_NAME}"
    with pytest.raises(RuntimeError, match="MODEL_NAME"):
        ComfyUIBackend._bind_io(
            workflow,
            input_images={"INPUT_IMAGE": "uploaded/input.png"},
            output_prefix="comic-enhancer/job",
        )


@pytest.mark.parametrize(
    ("workflow", "message"),
    [
        ({"1": {"class_type": "SaveImage", "inputs": {}}}, "exactly one"),
        ({"1": {"class_type": "LoadImage", "inputs": {}}}, "at least one"),
    ],
)
def test_bind_io_requires_load_and_save_nodes(workflow, message):
    with pytest.raises(RuntimeError, match=message):
        ComfyUIBackend._bind_io(
            workflow,
            input_images={"INPUT_IMAGE": "input.png"},
            output_prefix="output",
        )


def test_bind_io_uses_titles_for_reference_workflow():
    workflow = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "page.png"},
            "_meta": {"title": "INPUT_IMAGE"},
        },
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": "cover.png"},
            "_meta": {"title": "REFERENCE_IMAGE"},
        },
        "3": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0], "filename_prefix": "preset"},
        },
    }

    ComfyUIBackend._bind_io(
        workflow,
        input_images={
            "INPUT_IMAGE": "uploaded/page.png",
            "REFERENCE_IMAGE": "uploaded/cover.png",
        },
        output_prefix="comic-enhancer/job",
    )

    assert workflow["1"]["inputs"]["image"] == "uploaded/page.png"
    assert workflow["2"]["inputs"]["image"] == "uploaded/cover.png"


def test_flux2_candidate_workflow_has_concrete_four_step_reference_contract():
    path = PROJECT_ROOT / "workflows" / "flux2-klein-4b-reference-colorize.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))

    outputs = ComfyUIBackend._bind_io(
        workflow,
        input_images={
            "INPUT_IMAGE": "uploaded/page.png",
            "REFERENCE_IMAGE_1": "uploaded/elymas.png",
            "REFERENCE_IMAGE_2": "uploaded/luce.png",
            "REFERENCE_IMAGE_3": "uploaded/maris.png",
        },
        output_prefix="comic-enhancer/flux2-test",
    )

    assert workflow["5"]["inputs"]["unet_name"] == "flux-2-klein-4b-fp8.safetensors"
    assert workflow["6"]["inputs"]["clip_name"] == "qwen_3_4b.safetensors"
    assert workflow["28"]["inputs"]["steps"] == 4
    assert workflow["29"]["inputs"]["cfg"] == 1.0
    assert outputs == ("34",)


def test_adapter_workflow_cannot_escape_root(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        workflow_root=tmp_path,
    )
    adapter = AdapterManifest(
        adapter_id="unsafe",
        name="Unsafe",
        base_model="sd15-anime",
        revision="v1",
        file="unsafe.safetensors",
        workflows={"fast": "../unsafe.json"},
    )

    with pytest.raises(RuntimeError, match="escapes"):
        loader.load(ProcessOptions(mode="fast"), resolved(adapter))


def test_quality_mode_does_not_select_reference_workflow(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )

    loaded = loader.load(
        ProcessOptions(mode="quality"),
        resolved(),
        reference_available=True,
    )

    assert loaded.prompt["marker"]["inputs"]["value"] == "quality"
    assert loaded.reference_required is False
    assert loaded.model_profile == "sd15-colorize"


def test_manganinja_mode_selects_reference_workflow_when_available(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )

    loaded = loader.load(
        ProcessOptions(mode="manganinja"),
        resolved(),
        reference_available=True,
    )

    assert loaded.prompt["marker"]["inputs"]["value"] == "reference"
    assert loaded.reference_required is True
    assert loaded.model_profile == "manganinja-reference"


def test_manganinja_without_reference_uses_quality_adapter_workflow(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    adapter_workflow = tmp_path / "works" / "42-quality.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(adapter_workflow, marker="adapter-quality")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        workflow_root=tmp_path,
    )
    adapter = AdapterManifest(
        adapter_id="work-42",
        name="Work 42",
        base_model="sd15-anime",
        revision="v1",
        file="work.safetensors",
        workflows={"quality": "works/42-quality.json"},
    )

    loaded = loader.load(
        ProcessOptions(mode="manganinja"),
        resolved(adapter),
        reference_available=False,
    )

    assert loaded.prompt["marker"]["inputs"]["value"] == "adapter-quality"
    assert loaded.adapter_applied is True
    assert loaded.reference_required is False
    assert loaded.model_profile == "sd15-colorize-lora"


def test_reference_profile_requires_ready_marker(tmp_path, monkeypatch):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    ready = tmp_path / "MangaNinjia.ready"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )
    calls = []
    monkeypatch.setattr(
        "comic_enhancer.backends.httpx.get",
        lambda *args, **kwargs: calls.append(args) or SimpleNamespace(status_code=200),
    )
    backend = ComfyUIBackend(
        base_url="http://fast",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        reference_enabled=True,
        reference_ready_file=ready,
    )
    assets = reference_assets()

    assert backend.reference_profile_ready() is False
    assert calls == []

    ready.touch()
    assert backend.reference_profile_ready() is True
    assert backend.reference_profile_ready() is True
    assert len(calls) == 1


def test_quality_backend_does_not_probe_reference_profile(tmp_path, monkeypatch):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )
    backend = ComfyUIBackend(
        base_url="http://main",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        reference_enabled=True,
    )
    monkeypatch.setattr(
        backend,
        "_reference_ready",
        lambda: (_ for _ in ()).throw(AssertionError("reference probe")),
    )

    revision = backend.cache_revision(
        ProcessOptions(mode="quality"),
        resolved(),
        reference_assets(),
    )

    assert revision == loader.revision(ProcessOptions(mode="quality"), resolved())


def test_reference_cache_revision_includes_processing_algorithm(tmp_path, monkeypatch):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )
    backend = ComfyUIBackend(
        base_url="http://main",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        reference_enabled=True,
    )
    monkeypatch.setattr(backend, "_reference_ready", lambda: True)

    revision = backend.cache_revision(
        ProcessOptions(mode="manganinja"),
        resolved(),
        reference_assets(),
    )

    assert REFERENCE_PROCESSING_REVISION in revision
    write_workflow(quality, marker="quality-v2")
    changed_revision = backend.cache_revision(
        ProcessOptions(mode="manganinja"),
        resolved(),
        reference_assets(),
    )
    assert changed_revision != revision


def test_rejected_character_never_selects_reference_workflow(tmp_path, monkeypatch):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    ready = tmp_path / "MangaNinjia.ready"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    ready.touch()
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )
    monkeypatch.setattr(
        "comic_enhancer.backends.httpx.get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )
    backend = ComfyUIBackend(
        base_url="http://fast",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        reference_enabled=True,
        reference_ready_file=ready,
    )
    assets = reference_assets()
    assets.analysis.characters[0].match.status = "rejected"

    monkeypatch.setattr(backend, "_reference_ready", lambda: True)
    options = ProcessOptions(mode="manganinja")
    revision = backend.cache_revision(options, resolved(), assets)

    assert revision == loader.revision(
        options,
        resolved(),
        reference_available=False,
    )


def test_reference_failure_falls_back_to_selected_quality_workflow(tmp_path, monkeypatch):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    ready = tmp_path / "MangaNinjia.ready"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    ready.touch()
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )
    backend = ComfyUIBackend(
        base_url="http://main",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        reference_enabled=True,
        reference_ready_file=ready,
    )
    assets = reference_assets()
    monkeypatch.setattr(backend, "_reference_ready", lambda: True)
    monkeypatch.setattr(
        backend,
        "_process_reference_panels",
        lambda *args: (_ for _ in ()).throw(RuntimeError("reference failed")),
    )

    requested = {}

    class FakeResponse:
        content = b""

        def raise_for_status(self):
            return None

        def json(self):
            return {"prompt_id": "prompt-1"}

    class FakeClient:
        def __init__(self, *, base_url, timeout):
            requested["base_url"] = base_url

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, path, **kwargs):
            if path == "/upload/image":
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"name": "page.png", "subfolder": ""},
                )
            requested["prompt"] = kwargs["json"]["prompt"]
            return FakeResponse()

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("comic_enhancer.backends.httpx.Client", FakeClient)
    monkeypatch.setattr(
        backend,
        "_wait_for_output",
        lambda *args: {"filename": "result.png", "subfolder": "", "type": "output"},
    )
    monkeypatch.setattr(backend, "_upload", lambda *args: "uploaded/page.png")
    monkeypatch.setattr(
        "comic_enhancer.backends.Image.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not reached")),
    )

    with pytest.raises(AssertionError, match="not reached"):
        backend.process(
            assets,
            tmp_path / "output.webp",
            ProcessOptions(mode="manganinja"),
            resolved(),
        )

    assert requested["base_url"] == "http://main"
    assert requested["prompt"]["marker"]["inputs"]["value"] == "quality"


def test_reference_workflow_overlays_characters_on_quality_base(tmp_path, monkeypatch):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    reference = tmp_path / "reference.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(reference, marker="reference")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
        reference_quality_workflow=reference,
        workflow_root=tmp_path,
    )
    backend = ComfyUIBackend(
        base_url="http://main",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        reference_enabled=True,
    )
    assets = reference_assets()
    monkeypatch.setattr(backend, "_reference_ready", lambda: True)
    requested = {}

    def run_page_prompt(base_url, _image_bytes, workflow):
        requested["base_url"] = base_url
        requested["base_marker"] = workflow["marker"]["inputs"]["value"]
        return object()

    base_image = object()
    monkeypatch.setattr(backend, "_run_page_prompt", run_page_prompt)
    monkeypatch.setattr(
        backend,
        "_protect_source_structure",
        lambda *_args: base_image,
    )

    def process_reference(*args):
        requested["reference_marker"] = args[2]["marker"]["inputs"]["value"]
        requested["base_image"] = args[4]
        return SimpleNamespace(model_profile="manganinja-reference")

    monkeypatch.setattr(backend, "_process_reference_panels", process_reference)

    outcome = backend.process(
        assets,
        tmp_path / "output.webp",
        ProcessOptions(mode="manganinja"),
        resolved(),
    )

    assert outcome.model_profile == "manganinja-reference"
    assert requested == {
        "base_url": "http://main",
        "base_marker": "quality",
        "reference_marker": "reference",
        "base_image": base_image,
    }


def test_reference_board_and_target_points_keep_multiple_characters_aligned():
    assets = reference_assets()
    second = CharacterInstance(
        instance_id="character-2",
        cluster_id="cluster-2",
        box=BoundingBox(x1=60, y1=20, x2=90, y2=80),
        panel_index=0,
        match=CharacterMatch(
            character_id="bangumi:2",
            character_name="角色二",
            reference_url="https://example.com/2.png",
            status="accepted",
            confidence=0.8,
        ),
    )
    selected = [assets.analysis.characters[0], second]
    from io import BytesIO
    from PIL import Image

    stream1 = BytesIO()
    Image.new("RGB", (100, 200), "red").save(stream1, format="PNG")
    stream2 = BytesIO()
    Image.new("RGB", (200, 100), "blue").save(stream2, format="PNG")
    board, reference_points = ComfyUIBackend._reference_board(
        selected,
        {"bangumi:1": stream1.getvalue(), "bangumi:2": stream2.getvalue()},
    )
    target_points = ComfyUIBackend._target_points(
        selected,
        assets.analysis.panels[0],
    )

    with Image.open(BytesIO(board)) as image:
        assert image.size == (512, 512)
    assert len(reference_points) == len(target_points) == 8
    assert reference_points[0][1] == reference_points[3][1]
    assert reference_points[0][1] < reference_points[4][1]
    assert target_points[0][1] == target_points[3][1]
    assert target_points[0][1] < target_points[4][1]


def test_character_anchor_points_fall_back_when_too_small():
    panel = PanelRegion(
        panel_index=0,
        box=BoundingBox(x1=0, y1=0, x2=512, y2=512),
    )
    character = CharacterInstance(
        instance_id="tiny",
        cluster_id="cluster",
        box=BoundingBox(x1=10, y1=10, x2=11, y2=11),
    )

    points = ComfyUIBackend._target_points([character], panel)

    assert points == [[10, 10]]


def test_character_focus_region_keeps_context_inside_panel():
    character = CharacterInstance(
        instance_id="character-1",
        cluster_id="cluster-1",
        box=BoundingBox(x1=100, y1=100, x2=200, y2=300),
    )
    panel = PanelRegion(
        panel_index=3,
        box=BoundingBox(x1=50, y1=50, x2=400, y2=400),
        character_instance_ids=["character-1", "character-2"],
    )

    focus = ComfyUIBackend._character_focus_region(character, panel)

    assert focus.panel_index == 3
    assert focus.box == BoundingBox(x1=50, y1=50, x2=260, y2=350)
    assert focus.character_instance_ids == ["character-1"]


def test_character_focus_region_clamps_small_character_padding():
    character = CharacterInstance(
        instance_id="character-1",
        cluster_id="cluster-1",
        box=BoundingBox(x1=12, y1=14, x2=22, y2=34),
    )
    panel = PanelRegion(
        panel_index=0,
        box=BoundingBox(x1=0, y1=0, x2=100, y2=100),
    )

    focus = ComfyUIBackend._character_focus_region(character, panel)

    assert focus.box == BoundingBox(x1=0, y1=0, x2=46, y2=58)


def test_character_reference_uses_portrait_for_closeup_and_full_body_for_distance():
    references = {
        "character": b"default",
        "character:portrait": b"portrait",
        "character:full-body": b"full-body",
    }
    closeup = CharacterInstance(
        instance_id="closeup",
        cluster_id="cluster",
        box=BoundingBox(x1=0, y1=10, x2=314, y2=548),
        match=CharacterMatch(character_id="character"),
    )
    distance = CharacterInstance(
        instance_id="distance",
        cluster_id="cluster",
        box=BoundingBox(x1=86, y1=257, x2=301, y2=747),
        match=CharacterMatch(character_id="character"),
    )
    shoulder_closeup = CharacterInstance(
        instance_id="shoulder-closeup",
        cluster_id="cluster",
        box=BoundingBox(x1=50, y1=200, x2=250, y2=330),
        match=CharacterMatch(character_id="character"),
    )
    panel = PanelRegion(
        panel_index=0,
        box=BoundingBox(x1=0, y1=0, x2=306, y2=762),
    )

    assert ComfyUIBackend._character_reference(closeup, panel, references) == b"portrait"
    assert ComfyUIBackend._character_reference(
        shoulder_closeup, panel, references
    ) == b"portrait"
    assert ComfyUIBackend._character_reference(distance, panel, references) == b"full-body"


def test_reference_assets_require_character_segmentation_mask():
    assets = reference_assets()
    assets.analysis.characters[0].mask = None

    assert assets.has_panel_references is False


def test_bind_runtime_values_requires_titled_nodes():
    workflow = {
        "1": {
            "class_type": "MangaNinjaApiPoints",
            "inputs": {"points_json": "[]"},
            "_meta": {"title": "REFERENCE_POINTS", "runtime_input": "points_json"},
        },
        "2": {
            "class_type": "MangaNinjaApiPoints",
            "inputs": {"points_json": "[]"},
            "_meta": {"title": "TARGET_POINTS", "runtime_input": "points_json"},
        },
    }

    ComfyUIBackend._bind_runtime_values(
        workflow,
        {"REFERENCE_POINTS": "[[1,2]]", "TARGET_POINTS": "[[3,4]]"},
    )

    assert workflow["1"]["inputs"]["points_json"] == "[[1,2]]"
    assert workflow["2"]["inputs"]["points_json"] == "[[3,4]]"


@pytest.mark.parametrize(
    "name",
    ["sd15-colorize-fast.json", "sd15-colorize-quality.json"],
)
def test_shipped_workflows_are_self_contained_and_restore_dark_pixels(name):
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / name).read_text(encoding="utf-8")
    )

    assert "${" not in json.dumps(workflow)
    assert sum(node["class_type"] == "LoadImage" for node in workflow.values()) == 1
    assert sum(node["class_type"] == "SaveImage" for node in workflow.values()) >= 1
    assert any(
        node["class_type"] == "Image Blending Mode"
        and node["inputs"]["mode"] == "color"
        for node in workflow.values()
    )
    assert any(node["class_type"] == "ThresholdMask" for node in workflow.values())
    assert any(node["class_type"] == "ImageCompositeMasked" for node in workflow.values())


def test_shipped_cobra_workflow_declares_twelve_reference_inputs_and_fixed_values():
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / "cobra-colorize.json").read_text(
            encoding="utf-8"
        )
    )
    outputs = ComfyUIBackend._bind_io(
        workflow,
        input_images={
            "INPUT_IMAGE": "uploaded/page.png",
            **{
                f"REFERENCE_IMAGE_{index}": f"uploaded/ref-{index}.png"
                for index in range(1, 13)
            },
        },
        output_prefix="comic-enhancer/cobra-test",
    )

    assert outputs == ("15",)
    assert workflow["14"]["inputs"]["reference_count"] == 3
    assert workflow["14"]["inputs"]["steps"] == 10
    assert workflow["14"]["inputs"]["top_k"] == 3
    assert workflow["14"]["inputs"]["style"] == "line + shadow"


def test_manganinja_workflow_declares_page_and_reference_inputs():
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / "manganinja-reference-quality.json").read_text(
            encoding="utf-8"
        )
    )
    roles = {
        node.get("_meta", {}).get("title")
        for node in workflow.values()
        if node.get("class_type") == "LoadImage"
    }

    assert roles == {"INPUT_IMAGE", "REFERENCE_IMAGE"}
    sampler = next(
        node
        for node in workflow.values()
        if node["class_type"] == "MangaNinjiaSampler"
    )
    assert sampler["inputs"]["width"] == 512
    assert sampler["inputs"]["height"] == 512
    assert sampler["inputs"]["guidance_scale_ref"] <= 6
    assert sampler["inputs"]["guidance_scale_point"] <= 8
    assert sampler["inputs"]["guidance_scale_point"] > 0
    # The sampler must preprocess ordinary manga panels into lineart itself.
    assert sampler["inputs"]["is_lineart"] is False
    assert any(
        node.get("_meta", {}).get("title") == "REFERENCE_POINTS"
        for node in workflow.values()
    )
    assert any(node["class_type"] == "SaveImage" for node in workflow.values())
