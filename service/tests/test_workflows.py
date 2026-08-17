import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from comic_enhancer.inference import InferenceAssets
from comic_enhancer.inference.comfyui import (
    ComfyUIBackend,
    PresetWorkflowLoader,
    bind_io,
)
from comic_enhancer.inference.comfyui.strategies import (
    FLUX2_4B_COLOR_PROCESSING_REVISION,
    FLUX2_4B_SOURCE_PROCESSING_REVISION,
    FLUX2_9B_LORA_PROCESSING_REVISION,
    FLUX2_9B_FAST_PROCESSING_REVISION,
    FLUX2_9B_FAST_LOWRES_PROCESSING_REVISION,
    FLUX2_PROCESSING_REVISION,
    FastModeStrategy,
    Flux2ModeStrategy,
    Flux2CharacterModeStrategy,
    Flux2CharacterLineartModeStrategy,
    Flux2QuantModeStrategy,
    Flux24BSourceModeStrategy,
    Flux24BColorModeStrategy,
    Flux29BLoraModeStrategy,
    Flux29BFastModeStrategy,
    Flux29BFastLowresModeStrategy,
    QualityModeStrategy,
)
from comic_enhancer.models import ProcessOptions


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 方法说明：写入测试使用的最小 ComfyUI 工作流。
def write_workflow(
    path,
    *,
    marker,
    load_nodes=1,
    save_nodes=1,
    source_size_output=False,
):
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
    if source_size_output:
        workflow["1"]["_meta"] = {"title": "INPUT_IMAGE"}
        workflow["source-size"] = {
            "class_type": "GetImageSize",
            "inputs": {"image": ["1", 0]},
            "_meta": {"title": "SOURCE_IMAGE_SIZE"},
        }
        workflow["restore-source"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["marker", 0],
                "width": ["source-size", 0],
                "height": ["source-size", 1],
                "upscale_method": "lanczos",
                "crop": "disabled",
            },
            "_meta": {"title": "Restore Source Geometry"},
        }
        workflow["10"]["inputs"]["images"] = ["restore-source", 0]
        workflow["10"]["_meta"] = {"title": "OUTPUT_IMAGE"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow), encoding="utf-8")


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
        ),
    )

    expected = {
        "fast": FastModeStrategy,
        "quality": QualityModeStrategy,
        "flux2": Flux2ModeStrategy,
        "flux2_quant": Flux2QuantModeStrategy,
        "flux2_character": Flux2CharacterModeStrategy,
        "flux2_character_lineart": Flux2CharacterLineartModeStrategy,
        "flux2_9b_lora": Flux29BLoraModeStrategy,
        "flux2_9b_fast": Flux29BFastModeStrategy,
        "flux2_9b_fast_lowres": Flux29BFastLowresModeStrategy,
        "flux2_4b_source": Flux24BSourceModeStrategy,
        "flux2_4b_color": Flux24BColorModeStrategy,
    }
    for mode, strategy_type in expected.items():
        strategy = backend.mode_strategy(mode)
        assert isinstance(strategy, strategy_type)
    assert len({type(backend.mode_strategy(mode)) for mode in expected}) == len(expected)


@pytest.mark.parametrize(
    ("name", "mode", "loader_field", "model_profile", "revision"),
    [
        (
            "flux2-klein-9b-lora-colorize.json",
            "flux2_9b_lora",
            "flux2_9b_lora_workflow",
            "flux2-klein-9b-lora",
            FLUX2_9B_LORA_PROCESSING_REVISION,
        ),
        (
            "flux2-klein-9b-fast-colorize.json",
            "flux2_9b_fast",
            "flux2_9b_fast_workflow",
            "flux2-klein-9b-fast",
            FLUX2_9B_FAST_PROCESSING_REVISION,
        ),
        (
            "flux2-klein-9b-fast-lowres-colorize.json",
            "flux2_9b_fast_lowres",
            "flux2_9b_fast_lowres_workflow",
            "flux2-klein-9b-fast-lowres",
            FLUX2_9B_FAST_LOWRES_PROCESSING_REVISION,
        ),
        (
            "flux2-klein-4b-source-latent-colorize.json",
            "flux2_4b_source",
            "flux2_4b_source_workflow",
            "flux2-klein-4b-source",
            FLUX2_4B_SOURCE_PROCESSING_REVISION,
        ),
        (
            "flux2-klein-4b-color-colorize.json",
            "flux2_4b_color",
            "flux2_4b_color_workflow",
            "flux2-klein-4b-color",
            FLUX2_4B_COLOR_PROCESSING_REVISION,
        ),
    ],
)
# 方法说明：验证新增档位使用独立工作流、模型标识和缓存版本。
def test_new_flux2_modes_use_independent_workflow_contracts(
    tmp_path,
    monkeypatch,
    name,
    mode,
    loader_field,
    model_profile,
    revision,
):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    workflow_path = PROJECT_ROOT / "workflows" / name
    loader_options = {
        "fast_workflow": fast,
        "quality_workflow": quality,
        loader_field: workflow_path,
    }
    backend_options = {
        f"{mode}_enabled": True,
        f"{mode}_workflow": workflow_path,
    }
    loader = PresetWorkflowLoader(**loader_options)
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        **backend_options,
    )
    options = ProcessOptions(mode=mode)

    loaded = loader.load(options)
    monkeypatch.setattr(backend.transport, "ready", lambda: True)

    assert loaded.source == workflow_path.resolve()
    assert loaded.model_profile == model_profile
    assert backend.mode_available(mode) is True
    assert revision in backend.cache_revision(options)


# 方法说明：验证 9B LoRA 工作流固定使用四步空 latent 和三张参考图。
def test_shipped_flux2_9b_lora_workflow_has_quality_contract():
    workflow = json.loads(
        (
            PROJECT_ROOT / "workflows" / "flux2-klein-9b-lora-colorize.json"
        ).read_text(encoding="utf-8")
    )

    assert workflow["5"]["inputs"]["unet_name"] == (
        "FLUX.2-klein-9b-fp8-v2_flux2 Klein b9.safetensors"
    )
    assert workflow["6"]["inputs"]["clip_name"] == "qwen_3_8b_fp8mixed.safetensors"
    assert workflow["37"]["inputs"]["lora_name"] == (
        "Klein-9B/f2k_9B_lcs_consist_20260415.safetensors"
    )
    assert workflow["37"]["inputs"]["strength_model"] == 0.6
    assert workflow["27"]["class_type"] == "EmptyFlux2LatentImage"
    assert workflow["28"]["inputs"]["steps"] == 4
    assert workflow["29"]["inputs"]["cfg"] == 1.0
    assert [workflow[str(node)]["inputs"]["megapixels"] for node in (14, 18, 22)] == [
        0.25,
        0.25,
        0.25,
    ]
    assert workflow["34"]["inputs"]["images"] == ["35", 0]
    assert workflow["35"]["inputs"]["width"] == ["36", 0]
    assert workflow["35"]["inputs"]["height"] == ["36", 1]


# 方法说明：验证 9B 快速档只切换 FP8 快速矩阵计算且保持画质档其余参数。
def test_shipped_flux2_9b_fast_workflow_has_fast_dtype_contract():
    baseline = json.loads(
        (
            PROJECT_ROOT / "workflows" / "flux2-klein-9b-lora-colorize.json"
        ).read_text(encoding="utf-8")
    )
    workflow = json.loads(
        (
            PROJECT_ROOT / "workflows" / "flux2-klein-9b-fast-colorize.json"
        ).read_text(encoding="utf-8")
    )

    assert baseline["5"]["inputs"]["weight_dtype"] == "default"
    assert workflow["5"]["inputs"]["weight_dtype"] == "fp8_e4m3fn_fast"
    assert workflow["5"]["inputs"]["unet_name"] == baseline["5"]["inputs"]["unet_name"]
    assert workflow["6"]["inputs"] == baseline["6"]["inputs"]
    assert workflow["27"]["inputs"] == baseline["27"]["inputs"]
    assert workflow["28"]["inputs"] == baseline["28"]["inputs"]
    assert workflow["29"]["inputs"] == baseline["29"]["inputs"]
    assert workflow["37"]["inputs"] == baseline["37"]["inputs"]


# 方法说明：验证 9B 极速档只降低页面推理像素量并保持快速档其余参数。
def test_shipped_flux2_9b_fast_lowres_workflow_has_resolution_contract():
    baseline = json.loads(
        (
            PROJECT_ROOT / "workflows" / "flux2-klein-9b-fast-colorize.json"
        ).read_text(encoding="utf-8")
    )
    workflow = json.loads(
        (
            PROJECT_ROOT
            / "workflows"
            / "flux2-klein-9b-fast-lowres-colorize.json"
        ).read_text(encoding="utf-8")
    )

    assert baseline["10"]["inputs"]["megapixels"] == 0.85
    assert workflow["10"]["inputs"]["megapixels"] == 0.70
    unchanged_nodes = (
        "5",
        "6",
        "7",
        "8",
        "9",
        "14",
        "18",
        "22",
        "27",
        "28",
        "29",
        "37",
    )
    for node_id in unchanged_nodes:
        assert workflow[node_id]["inputs"] == baseline[node_id]["inputs"]


# 方法说明：验证 4B 结构稳定工作流固定使用 source latent 和低降噪参数。
def test_shipped_flux2_4b_source_workflow_has_structure_contract():
    workflow = json.loads(
        (
            PROJECT_ROOT
            / "workflows"
            / "flux2-klein-4b-source-latent-colorize.json"
        ).read_text(encoding="utf-8")
    )

    assert workflow["31"]["inputs"]["lora_name"] == (
        "f2k_4B_consist_20260314.safetensors"
    )
    assert workflow["31"]["inputs"]["strength_model"] == 0.35
    assert workflow["11"]["class_type"] == "VAEEncode"
    assert workflow["26"]["inputs"]["latent_image"] == ["11", 0]
    assert workflow["26"]["inputs"]["steps"] == 4
    assert workflow["26"]["inputs"]["cfg"] == 1.3
    assert workflow["26"]["inputs"]["denoise"] == 0.65
    assert [workflow[str(node)]["inputs"]["megapixels"] for node in (14, 18, 22)] == [
        0.15,
        0.15,
        0.15,
    ]
    assert workflow["30"]["inputs"]["images"] == ["29", 0]
    assert workflow["29"]["inputs"]["width"] == ["28", 0]
    assert workflow["29"]["inputs"]["height"] == ["28", 1]


# 方法说明：验证 4B 色彩增强工作流只调整 LoRA 强度且保留结构参数。
def test_shipped_flux2_4b_color_workflow_has_color_contract():
    workflow = json.loads(
        (
            PROJECT_ROOT / "workflows" / "flux2-klein-4b-color-colorize.json"
        ).read_text(encoding="utf-8")
    )

    assert workflow["31"]["inputs"]["lora_name"] == (
        "f2k_4B_consist_20260314.safetensors"
    )
    assert workflow["31"]["inputs"]["strength_model"] == 0.25
    assert workflow["26"]["inputs"]["latent_image"] == ["11", 0]
    assert workflow["26"]["inputs"]["steps"] == 4
    assert workflow["26"]["inputs"]["cfg"] == 1.3
    assert workflow["26"]["inputs"]["denoise"] == 0.65
    assert [workflow[str(node)]["inputs"]["megapixels"] for node in (14, 18, 22)] == [
        0.15,
        0.15,
        0.15,
    ]


# 方法说明：验证线稿保真工作流保持 0.85MP 四步生成并在末端恢复原图尺寸。
def test_shipped_character_lineart_workflow_has_source_structure_contract():
    path = (
        PROJECT_ROOT
        / "workflows"
        / "flux2-klein-4b-qwen3-vl-character-lineart-colorize.json"
    )
    workflow = json.loads(path.read_text(encoding="utf-8"))
    load_titles = {
        node.get("_meta", {}).get("title")
        for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type") == "LoadImage"
    }

    assert load_titles == {
        "INPUT_IMAGE",
        "REFERENCE_IMAGE_1",
        "REFERENCE_IMAGE_2",
        "REFERENCE_IMAGE_3",
    }
    assert workflow["10"]["inputs"]["megapixels"] == 0.85
    assert workflow["28"]["inputs"]["steps"] == 4
    assert workflow["32"]["inputs"]["latent_image"] == ["27", 0]
    assert workflow["27"]["class_type"] == "EmptyFlux2LatentImage"
    assert workflow["34"]["inputs"]["images"] == ["35", 0]
    assert workflow["35"]["inputs"]["width"] == ["36", 0]
    assert workflow["35"]["inputs"]["height"] == ["36", 1]
    assert workflow["36"]["inputs"]["image"] == ["1", 0]
    assert (
        "source-aligned low-frequency color and tone layer"
        in workflow["8"]["inputs"]["text"]
    )


@pytest.mark.parametrize(
    "name",
    [
        "flux2-klein-4b-character-no-reference-colorize.json",
        "flux2-klein-4b-character-lineart-no-reference-colorize.json",
    ],
)
# 方法说明：验证无参考工作流只接收原图并保留 FLUX.2 尺寸恢复采样链路。
def test_shipped_character_no_reference_workflows_are_single_input(name):
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / name).read_text(encoding="utf-8")
    )
    titles = {
        node.get("_meta", {}).get("title")
        for node in workflow.values()
        if isinstance(node, dict)
    }

    assert "INPUT_IMAGE" in titles
    assert not any(title and title.startswith("REFERENCE_IMAGE_") for title in titles)
    assert sum(node.get("class_type") == "LoadImage" for node in workflow.values()) == 1
    assert sum(node.get("class_type") == "ReferenceLatent" for node in workflow.values()) == 2
    assert workflow["27"]["class_type"] == "EmptyFlux2LatentImage"
    assert workflow["28"]["class_type"] == "Flux2Scheduler"
    assert workflow["28"]["inputs"]["steps"] == 4
    assert workflow["29"]["inputs"]["positive"] == ["12", 0]
    assert workflow["29"]["inputs"]["negative"] == ["13", 0]
    assert workflow["35"]["class_type"] == "ImageScale"
    assert workflow["36"]["inputs"]["image"] == ["1", 0]
    assert "TEXT AND GRAPHICS LOCK" in workflow["8"]["inputs"]["text"]


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
        )


# 方法说明：验证最高质量档使用三张参考图并由工作流恢复到原图尺寸。
def test_flux2_backend_uses_three_references_and_restores_source_size(
    tmp_path, monkeypatch
):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    flux2 = tmp_path / "flux2.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    write_workflow(
        flux2,
        marker="flux2",
        load_nodes=4,
        source_size_output=True,
    )
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=PresetWorkflowLoader(
            fast_workflow=fast,
            quality_workflow=quality,
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
        if prepare_workflow is not None:
            prepare_workflow(workflow)
        captured["input_images"] = input_images
        captured["workflow"] = workflow
        captured["output_prefix"] = output_prefix
        captured["prepare_workflow"] = prepare_workflow
        return Image.new("RGB", (8, 12), (220, 80, 130))

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
    )

    assert outcome.model_profile == "flux2-klein-4b"
    assert outcome.reference_applied is True
    reference_values = {
        captured["input_images"][f"REFERENCE_IMAGE_{index}"]
        for index in range(1, 4)
    }
    assert len(reference_values) == 3
    assert captured["workflow"]["marker"]["inputs"]["value"] == "flux2"
    assert captured["prepare_workflow"] is not None
    assert captured["workflow"]["source-size"]["inputs"]["image"] == ["1", 0]
    assert captured["workflow"]["10"]["inputs"]["images"] == [
        "restore-source",
        0,
    ]
    assert FLUX2_PROCESSING_REVISION in backend.cache_revision(
        ProcessOptions(mode="flux2"),
        assets,
    )
    with Image.open(output_path) as result:
        assert result.size == source.size
        # 服务端不再二次插值或混合原图，保留 ComfyUI 最终颜色。
        pixel = result.getpixel((4, 6))
        assert pixel[0] > 180
        assert pixel[2] > 90


# 方法说明：验证加载器会选择快速档和质量档的独立完整工作流。
def test_loader_selects_complete_workflow_for_each_mode(tmp_path):
    fast = tmp_path / "fast.json"
    quality = tmp_path / "quality.json"
    write_workflow(fast, marker="fast")
    write_workflow(quality, marker="quality")
    loader = PresetWorkflowLoader(
        fast_workflow=fast,
        quality_workflow=quality,
    )

    fast_loaded = loader.load(ProcessOptions(mode="fast"))
    quality_loaded = loader.load(ProcessOptions(mode="quality"))

    assert fast_loaded.prompt["marker"]["inputs"]["value"] == "fast"
    assert quality_loaded.prompt["marker"]["inputs"]["value"] == "quality"
    assert loader.revision(ProcessOptions(mode="fast")) != loader.revision(
        ProcessOptions(mode="quality")
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
    assert workflow["10"]["inputs"]["megapixels"] == 0.85
    assert workflow["35"]["class_type"] == "ImageScale"
    assert workflow["35"]["inputs"]["image"] == ["33", 0]
    assert workflow["35"]["inputs"]["width"] == ["36", 0]
    assert workflow["35"]["inputs"]["height"] == ["36", 1]
    assert workflow["36"]["inputs"]["image"] == ["1", 0]
    assert workflow["34"]["inputs"]["images"] == ["35", 0]
    assert outputs == ("34",)


@pytest.mark.parametrize(
    "name",
    [
        "flux2-klein-4b-reference-colorize.json",
        "flux2-klein-4b-reference-colorize-qwen3-fp8.json",
    ],
)
# 方法说明：验证两个 FLUX.2 工作流以提示词保护文字并保持独立输出链路。
def test_shipped_flux2_workflows_use_prompt_protection_and_direct_output(name):
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / name).read_text(encoding="utf-8")
    )

    assert not any(
        node["class_type"] == "Image Blending Mode"
        for node in workflow.values()
    )
    prompt = workflow["8"]["inputs"]["text"]
    normalized_prompt = prompt.lower()
    assert "text and graphics" in normalized_prompt
    assert "source" in normalized_prompt and (
        "unalterable" in normalized_prompt or "immutable" in normalized_prompt
    )
    assert "glyph" in normalized_prompt
    assert "cel" in normalized_prompt
    assert "punctuation" in workflow["9"]["inputs"]["text"].lower()
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
    expected_output = (
        ["35", 0]
        if name == "flux2-klein-4b-reference-colorize.json"
        else ["33", 0]
    )
    assert workflow["34"]["inputs"]["images"] == expected_output


@pytest.mark.parametrize(
    ("name", "positive_tokens", "negative_tokens"),
    [
        (
            "flux2-klein-4b-reference-colorize.json",
            (
                "complete scene colorization",
                "two or three restrained tonal levels",
                "not as a requirement for the final image to remain gray",
            ),
            ("copied reference background", "new mountain", "grayscale wash"),
        ),
        (
            "flux2-klein-4b-qwen3-vl-character-colorize.json",
            (
                "confirmed character identity and palette consistency",
                "CHARACTER REFERENCE MAP AND PALETTE GUIDE",
                "color the existing person independently",
            ),
            (
                "wrong character palette",
                "character palette drift",
                "palette copied to background",
            ),
        ),
        (
            "flux2-klein-4b-qwen3-vl-character-lineart-colorize.json",
            (
                "source-aligned low-frequency color and tone layer",
                "do not regenerate high-frequency detail",
                "Do not leave the sky or major background surfaces as a gray wash",
            ),
            ("new contour", "new ridge", "gray sky"),
        ),
    ],
)
# 方法说明：验证三个 FLUX.2 档位的提示词分别承接画质、角色稳定和线稿保真职责。
def test_shipped_flux2_mode_prompts_match_strategy(
    name,
    positive_tokens,
    negative_tokens,
):
    workflow = json.loads(
        (PROJECT_ROOT / "workflows" / name).read_text(encoding="utf-8")
    )
    positive = workflow["8"]["inputs"]["text"]
    negative = workflow["9"]["inputs"]["text"]

    assert "PRIORITY ORDER" in positive
    assert "TEXT AND GRAPHICS LOCK" in positive
    assert all(token in positive for token in positive_tokens)
    assert all(token in negative for token in negative_tokens)


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
