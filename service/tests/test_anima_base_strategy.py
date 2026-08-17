import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from comic_enhancer.inference.comfyui.strategies.anima_base import (
    ANIMA_BASE_CFG,
    ANIMA_BASE_LLLITE_STRENGTH,
    ANIMA_BASE_PROCESSING_REVISION,
    ANIMA_BASE_STEPS,
    AnimaBaseModeStrategy,
)
from comic_enhancer.inference.comfyui.workflows import LoadedWorkflow
from comic_enhancer.inference.contracts import InferenceAssets


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 方法说明：生成测试使用的 PNG 图片字节。
def png_bytes(size: tuple[int, int] = (32, 48)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, "white").save(stream, format="PNG")
    return stream.getvalue()


class FakeWorkflowLoader:
    """提供 Anima Base 策略测试所需的最小工作流能力。"""

    # 方法说明：声明专用 Anima Base 工作流可用。
    def supports_anima_base(self) -> bool:
        return True

    # 方法说明：返回测试工作流和真实模型标识。
    def load(self, options):
        return LoadedWorkflow(
            prompt={"1": {"class_type": "LoadImage"}},
            source=Path("anima-base-v1-lineart-colorize.json"),
            model_profile="anima-base-v1.0-lllite-lineart",
        )

    # 方法说明：返回固定工作流版本用于验证缓存契约。
    def revision(self, options) -> str:
        return "workflow-revision"


class FakeTransport:
    """提供 Anima Base 策略测试所需的最小传输能力。"""

    def __init__(self):
        self.calls = []

    # 方法说明：按开关和工作流状态返回档位可用性。
    def profile_ready(self, mode, *, enabled, workflow_supported):
        self.calls.append(("profile_ready", mode, enabled, workflow_supported))
        return enabled and workflow_supported

    # 方法说明：返回模拟的原尺寸 ComfyUI 结果。
    def run(self, workflow, *, input_images, output_prefix, prepare_workflow=None):
        self.calls.append(
            {
                "workflow": workflow,
                "input_images": input_images,
                "output_prefix": output_prefix,
                "prepare_workflow": prepare_workflow,
            }
        )
        return Image.new("RGB", (32, 48), (120, 180, 240))


# 方法说明：验证提交的 Anima Base 工作流使用 LLLite 线稿控制和固定采样参数。
def test_shipped_workflow_has_anima_base_lineart_contract():
    path = PROJECT_ROOT / "workflows" / "anima-base-v1-lineart-colorize.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))

    assert sum(node["class_type"] == "LoadImage" for node in workflow.values()) == 1
    assert sum(node["class_type"] == "SaveImage" for node in workflow.values()) == 1
    assert workflow["2"]["inputs"]["unet_name"] == "anima-base-v1.0.safetensors"
    assert workflow["3"]["inputs"]["clip_name"] == "qwen_3_06b_base.safetensors"
    assert workflow["4"]["inputs"]["vae_name"] == "qwen_image_vae.safetensors"
    assert workflow["5"]["class_type"] == "AnimaLLLiteApply_sdscripts"
    assert workflow["5"]["inputs"]["lllite_name"] == (
        "anima-lllite-lineart-1.safetensors"
    )
    assert workflow["5"]["inputs"]["strength"] == ANIMA_BASE_LLLITE_STRENGTH
    assert workflow["7"]["inputs"]["megapixels"] == 1.0
    assert workflow["13"]["inputs"]["steps"] == ANIMA_BASE_STEPS
    assert workflow["13"]["inputs"]["cfg"] == ANIMA_BASE_CFG
    assert workflow["13"]["inputs"]["sampler_name"] == "er_sde"
    assert workflow["13"]["inputs"]["scheduler"] == "simple"
    assert workflow["13"]["inputs"]["denoise"] == 1.0
    assert workflow["10"]["class_type"] == "EmptyLatentImage"
    assert workflow["15"]["inputs"]["width"] == ["16", 0]
    assert workflow["15"]["inputs"]["height"] == ["16", 1]
    assert "${" not in json.dumps(workflow)
    assert not any(node["class_type"] == "VAEEncode" for node in workflow.values())


# 方法说明：验证策略只上传原图且不执行角色参考或服务端后处理。
def test_strategy_processes_single_input_without_postprocessing(tmp_path):
    loader = FakeWorkflowLoader()
    transport = FakeTransport()
    strategy = AnimaBaseModeStrategy(
        enabled=True,
        workflow_path=PROJECT_ROOT / "workflows" / "anima-base-v1-lineart-colorize.json",
        workflow_loader=loader,
        transport=transport,
    )
    assets = InferenceAssets(
        image_bytes=png_bytes(),
        character_references={"ignored": png_bytes((8, 8))},
    )
    output_path = tmp_path / "anima-base.webp"

    outcome = strategy.process(
        assets,
        output_path,
        SimpleNamespace(mode="anima_base"),
    )

    assert outcome.reference_applied is False
    assert outcome.model_profile == "anima-base-v1.0-lllite-lineart"
    assert transport.calls[1]["input_images"].keys() == {"INPUT_IMAGE"}
    assert transport.calls[1]["prepare_workflow"] is None
    assert ANIMA_BASE_PROCESSING_REVISION in strategy.cache_revision(
        SimpleNamespace(mode="anima_base"), assets
    )
    with Image.open(output_path) as output:
        assert output.size == (32, 48)


# 方法说明：验证加载器缺少专用能力时不会误用其他档位工作流。
def test_strategy_requires_dedicated_loader_capability(tmp_path):
    loader = FakeWorkflowLoader()
    loader.supports_anima_base = None
    strategy = AnimaBaseModeStrategy(
        enabled=True,
        workflow_path=PROJECT_ROOT / "workflows" / "anima-base-v1-lineart-colorize.json",
        workflow_loader=loader,
        transport=FakeTransport(),
    )

    assert strategy.available() is False
    with pytest.raises(RuntimeError, match="服务未就绪"):
        strategy.process(
            InferenceAssets(image_bytes=png_bytes()),
            tmp_path / "unused.webp",
            SimpleNamespace(mode="anima_base"),
        )
