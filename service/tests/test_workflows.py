import json
from pathlib import Path

import pytest

from comic_enhancer.backends import ComfyUIBackend
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
        input_image="uploaded/input.png",
        output_prefix="comic-enhancer/job",
    )

    assert workflow["5"]["inputs"]["image"] == "uploaded/input.png"
    assert workflow["18"]["inputs"]["filename_prefix"] == "comic-enhancer/job"
    assert output_nodes == ("18",)

    workflow["5"]["inputs"]["model"] = "${MODEL_NAME}"
    with pytest.raises(RuntimeError, match="MODEL_NAME"):
        ComfyUIBackend._bind_io(
            workflow,
            input_image="uploaded/input.png",
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
            input_image="input.png",
            output_prefix="output",
        )


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
