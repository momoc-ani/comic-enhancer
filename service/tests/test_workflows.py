import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from comic_enhancer.backends import (
    FLUX2_PROCESSING_REVISION,
    ComfyUIBackend,
    InferenceAssets,
)
from comic_enhancer.models import (
    AdapterManifest,
    AdapterSource,
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


def test_flux2_backend_uses_three_references_and_restores_source_size(
    tmp_path, monkeypatch
):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    flux2 = tmp_path / "flux2.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(flux2, marker="flux2", load_nodes=4)
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=PresetWorkflowLoader(
            fast_workflow=fast,
            quality_workflow=quality,
            workflow_root=tmp_path,
            flux2_workflow=flux2,
        ),
        flux2_enabled=True,
        flux2_workflow=flux2,
        flux2_reference_limit=3,
    )
    monkeypatch.setattr(backend, "flux2_profile_ready", lambda: True)
    monkeypatch.setattr(backend, "_unload_cobra_worker", lambda: None)

    def reject_preset_structure(*_args):
        raise AssertionError("FLUX.2 must not use the preset structure policy")

    monkeypatch.setattr(
        backend,
        "_protect_source_structure",
        reject_preset_structure,
    )
    captured = {}

    def run_flux2(image_bytes, references, workflow):
        captured["image_bytes"] = image_bytes
        captured["references"] = references
        captured["workflow"] = workflow
        return Image.new("RGB", (16, 24), (220, 80, 130))

    monkeypatch.setattr(backend, "_run_flux2_prompt", run_flux2)
    source = Image.new("RGB", (8, 12), "white")
    assets = InferenceAssets(
        image_bytes=png_bytes(source),
        character_references={
            f"character-{index}": png_bytes(
                Image.new("RGB", (4, 6), (index * 40, 20, 100))
            )
            for index in range(1, 5)
        },
    )
    output_path = tmp_path / "flux2.webp"

    outcome = backend.process(
        assets,
        output_path,
        ProcessOptions(mode="flux2"),
        resolved(),
    )

    assert outcome.model_profile == "flux2-klein-4b"
    assert outcome.reference_applied is True
    assert len(captured["references"]) == 3
    assert captured["workflow"]["marker"]["inputs"]["value"] == "flux2"
    assert FLUX2_PROCESSING_REVISION in backend.cache_revision(
        ProcessOptions(mode="flux2"),
        resolved(),
        assets,
    )
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
