import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from comic_enhancer.inference import InferenceAssets, InferenceOutcome
from comic_enhancer.inference.comfyui import (
    ComfyUIBackend,
    PresetWorkflowLoader,
    bind_io,
)
from comic_enhancer.inference.comfyui.strategies import (
    FLUX2_PROCESSING_REVISION,
    FastModeStrategy,
    Flux2ModeStrategy,
    Flux2QuantModeStrategy,
    QualityModeStrategy,
)
from comic_enhancer.models import (
    AdapterManifest,
    AdapterSource,
    ProcessOptions,
    ResolvedAdapter,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 方法说明：写入测试使用的最小 ComfyUI 工作流。
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


# 方法说明：创建测试使用的适配器解析结果。
def resolved(adapter=None):
    return ResolvedAdapter(
        source=AdapterSource.WORK if adapter else AdapterSource.NONE,
        adapter=adapter,
        reason="test",
    )


# 方法说明：生成测试使用的 PNG 图片字节。
def png_bytes(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


# 方法说明：验证每个 ComfyUI 处理档位注册独立的策略实现。
def test_comfyui_backend_registers_one_strategy_implementation_per_mode(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=PresetWorkflowLoader(
            fast_workflow=fast,
            quality_workflow=quality,
            workflow_root=tmp_path,
        ),
    )

    expected = {
        "fast": FastModeStrategy,
        "quality": QualityModeStrategy,
        "flux2": Flux2ModeStrategy,
        "flux2_quant": Flux2QuantModeStrategy,
    }
    for mode, strategy_type in expected.items():
        strategy = backend.mode_strategy(mode)
        assert isinstance(strategy, strategy_type)
        expected_workflow = mode if mode in {"fast", "quality"} else "quality"
        assert strategy.adapter_policy().required_workflow == expected_workflow
    assert len({type(backend.mode_strategy(mode)) for mode in expected}) == len(expected)


@pytest.mark.parametrize("mode", ["flux2", "flux2_quant"])
# 方法说明：验证 FLUX.2 档位失败时直接报错且不执行质量档回退。
def test_flux2_strategies_do_not_fallback_to_quality(tmp_path, monkeypatch, mode):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    flux2 = tmp_path / "flux2.json"
    flux2_quant = tmp_path / "flux2-quant.json"
    for path, marker in (
        (fast, "fast"),
        (quality, "quality"),
        (flux2, "flux2"),
        (flux2_quant, "flux2-quant"),
    ):
        write_workflow(path, marker=marker, load_nodes=4 if "flux2" in marker else 1)
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=PresetWorkflowLoader(
            fast_workflow=fast,
            quality_workflow=quality,
            workflow_root=tmp_path,
            flux2_workflow=flux2,
            flux2_quant_workflow=flux2_quant,
        ),
        flux2_enabled=True,
        flux2_workflow=flux2,
        flux2_quant_enabled=True,
        flux2_quant_workflow=flux2_quant,
    )
    monkeypatch.setattr(backend.mode_strategy(mode), "available", lambda: True)

    # 方法说明：模拟 FLUX.2 传输失败。
    def fail_transport(*_args, **_kwargs):
        raise RuntimeError("flux2 failed")

    monkeypatch.setattr(backend.transport, "run", fail_transport)
    assets = InferenceAssets(
        image_bytes=png_bytes(Image.new("RGB", (8, 12), "white")),
        character_references={
            "character": png_bytes(Image.new("RGB", (4, 6), "red"))
        },
    )

    with pytest.raises(RuntimeError, match="flux2 failed"):
        backend.process(
            assets,
            tmp_path / f"{mode}.webp",
            ProcessOptions(mode=mode),
            resolved(),
        )


# 方法说明：验证 FLUX.2 使用三张参考图并精确恢复为原图两倍尺寸。
def test_flux2_backend_uses_three_references_and_restores_source_size_at_two_x(
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
    monkeypatch.setattr(backend.mode_strategy("flux2"), "available", lambda: True)
    captured = {}

    # 方法说明：模拟传输层执行 FLUX.2 工作流并记录档位输入。
    def run_flux2(
        workflow,
        *,
        input_images,
        output_prefix,
        prepare_workflow=None,
    ):
        captured["input_images"] = input_images
        captured["workflow"] = workflow
        captured["output_prefix"] = output_prefix
        captured["prepare_workflow"] = prepare_workflow
        return Image.new("RGB", (16, 24), (220, 80, 130))

    monkeypatch.setattr(backend.transport, "run", run_flux2)
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
    reference_values = {
        captured["input_images"][f"REFERENCE_IMAGE_{index}"]
        for index in range(1, 4)
    }
    assert len(reference_values) == 3
    assert captured["workflow"]["marker"]["inputs"]["value"] == "flux2"
    assert FLUX2_PROCESSING_REVISION in backend.cache_revision(
        ProcessOptions(mode="flux2"),
        resolved(),
        assets,
    )
    with Image.open(output_path) as result:
        assert result.size == (source.width * 2, source.height * 2)
        # 后端只规范尺寸，不应再次混合原图而破坏 ComfyUI 工作流的最终颜色。
        pixel = result.getpixel((8, 12))
        assert pixel[0] > 180
        assert pixel[2] > 90


# 方法说明：验证加载器会选择指定档位和完整适配器工作流。
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


# 方法说明：验证工作流输入输出绑定会拒绝未替换占位符。
def test_bind_io_discovers_nodes_and_rejects_placeholders():
    workflow = {
        "5": {"class_type": "LoadImage", "inputs": {"image": "preset.png"}},
        "18": {
            "class_type": "SaveImage",
            "inputs": {"images": ["5", 0], "filename_prefix": "preset"},
        },
    }

    output_nodes = bind_io(
        workflow,
        input_images={"INPUT_IMAGE": "uploaded/input.png"},
        output_prefix="comic-enhancer/job",
    )

    assert workflow["5"]["inputs"]["image"] == "uploaded/input.png"
    assert workflow["18"]["inputs"]["filename_prefix"] == "comic-enhancer/job"
    assert output_nodes == ("18",)

    workflow["5"]["inputs"]["model"] = "${MODEL_NAME}"
    with pytest.raises(RuntimeError, match="MODEL_NAME"):
        bind_io(
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
# 方法说明：验证工作流绑定要求存在加载和保存节点。
def test_bind_io_requires_load_and_save_nodes(workflow, message):
    with pytest.raises(RuntimeError, match=message):
        bind_io(
            workflow,
            input_images={"INPUT_IMAGE": "input.png"},
            output_prefix="output",
        )


# 方法说明：验证参考图工作流可通过节点标题完成绑定。
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

    bind_io(
        workflow,
        input_images={
            "INPUT_IMAGE": "uploaded/page.png",
            "REFERENCE_IMAGE": "uploaded/cover.png",
        },
        output_prefix="comic-enhancer/job",
    )

    assert workflow["1"]["inputs"]["image"] == "uploaded/page.png"
    assert workflow["2"]["inputs"]["image"] == "uploaded/cover.png"


# 方法说明：验证 FLUX.2 工作流恢复旧基准的四步空 latent 直出契约。
def test_flux2_candidate_workflow_has_baseline_direct_output_contract():
    path = PROJECT_ROOT / "workflows" / "flux2-klein-4b-reference-colorize.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))

    outputs = bind_io(
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
    assert workflow["27"]["class_type"] == "EmptyFlux2LatentImage"
    assert workflow["32"]["inputs"]["sigmas"] == ["28", 0]
    assert workflow["32"]["inputs"]["latent_image"] == ["27", 0]
    assert workflow["29"]["inputs"]["cfg"] == 1.0
    assert outputs == ("34",)


@pytest.mark.parametrize(
    "name",
    [
        "flux2-klein-4b-reference-colorize.json",
        "flux2-klein-4b-reference-colorize-qwen3-fp8.json",
    ],
)
# 方法说明：验证两个 FLUX.2 工作流以提示词保护文字，并直接保存未后处理结果。
def test_shipped_flux2_workflows_use_prompt_protection_and_direct_output(name):
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / name).read_text(encoding="utf-8")
    )

    assert not any(
        node["class_type"] == "Image Blending Mode"
        for node in workflow.values()
    )
    prompt = workflow["8"]["inputs"]["text"]
    assert "TEXT AND GRAPHICS LOCK" in prompt
    assert "immutable source regions" in prompt
    assert "Copy every glyph" in prompt
    assert "professional anime-style cel coloring" in prompt
    assert "changed punctuation" in workflow["9"]["inputs"]["text"]
    assert not any(
        node["class_type"]
        in {
            "ImageCompositeMasked",
            "UpscaleModelLoader",
            "ImageUpscaleWithModel",
            "ImageScaleBy",
        }
        for node in workflow.values()
    )
    assert workflow["34"]["inputs"]["images"] == ["33", 0]


# 方法说明：验证适配器工作流路径不能逃逸配置根目录。
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
# 方法说明：验证内置工作流自包含并能够恢复暗色像素。
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
