import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from comic_enhancer.inference.comfyui.strategies.anima_2_9b import (
    ANIMA_2_9B_CFG,
    ANIMA_2_9B_DENOISE,
    ANIMA_2_9B_PROCESSING_REVISION,
    ANIMA_2_9B_STEPS,
    Anima29BModeStrategy,
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
    """提供策略测试所需的最小工作流加载能力。"""

    # 方法说明：声明独立 Anima-2.9B 工作流能力可用。
    def supports_anima_2_9b(self) -> bool:
        return True

    # 方法说明：返回测试工作流和真实模型标识。
    def load(self, options):
        return LoadedWorkflow(
            prompt={"1": {"class_type": "LoadImage"}},
            source=Path("anima-2.9b-img2img-colorize.json"),
            model_profile="anima-2.9b-preview-v1",
        )

    # 方法说明：返回固定工作流版本，验证采样参数进入缓存契约。
    def revision(self, options) -> str:
        return "workflow-revision"


class FakeTransport:
    """提供策略测试所需的最小 ComfyUI 传输能力。"""

    def __init__(self):
        self.calls = []

    # 方法说明：返回测试服务和工作流均已准备就绪。
    def profile_ready(self, mode, *, enabled, workflow_supported):
        self.calls.append(("profile_ready", mode, enabled, workflow_supported))
        return enabled and workflow_supported

    # 方法说明：返回与原图一致尺寸的模拟生成结果。
    def run(self, workflow, *, input_images, output_prefix, prepare_workflow=None):
        self.calls.append(
            {
                "workflow": workflow,
                "input_images": input_images,
                "output_prefix": output_prefix,
                "prepare_workflow": prepare_workflow,
            }
        )
        return Image.new("RGB", (32, 48), (240, 120, 160))


# 方法说明：验证 Anima-2.9B 工作流包含完整图生图采样契约。
def test_shipped_workflow_has_anima_img2img_contract():
    path = PROJECT_ROOT / "workflows" / "anima-2.9b-img2img-colorize.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))

    assert sum(node["class_type"] == "LoadImage" for node in workflow.values()) == 1
    assert sum(node["class_type"] == "SaveImage" for node in workflow.values()) == 1
    assert workflow["2"]["inputs"]["unet_name"] == "Anima-2.9B-preview-v1.safetensors"
    assert workflow["3"]["inputs"]["clip_name"] == "qwen_3_06b_base.safetensors"
    assert workflow["4"]["inputs"]["vae_name"] == "qwen_image_vae.safetensors"
    assert workflow["5"]["class_type"] == "ModelSamplingAuraFlow"
    assert workflow["5"]["inputs"]["shift"] == 3.0
    assert workflow["9"]["class_type"] == "VAEEncode"
    assert workflow["10"]["inputs"]["latent_image"] == ["9", 0]
    assert workflow["10"]["inputs"]["steps"] == ANIMA_2_9B_STEPS
    assert workflow["10"]["inputs"]["cfg"] == ANIMA_2_9B_CFG
    assert workflow["10"]["inputs"]["sampler_name"] == "euler"
    assert workflow["10"]["inputs"]["scheduler"] == "sgm_uniform"
    assert workflow["10"]["inputs"]["denoise"] == ANIMA_2_9B_DENOISE
    assert workflow["14"]["inputs"]["images"] == ["13", 0]
    assert workflow["13"]["inputs"]["width"] == ["12", 0]
    assert workflow["13"]["inputs"]["height"] == ["12", 1]
    assert "${" not in json.dumps(workflow)
    assert not any(node["class_type"] == "ReferenceLatent" for node in workflow.values())


# 方法说明：验证策略只上传原图、不使用角色参考图并保持工作流直出。
def test_strategy_processes_single_input_without_postprocessing(tmp_path):
    loader = FakeWorkflowLoader()
    transport = FakeTransport()
    strategy = Anima29BModeStrategy(
        enabled=True,
        workflow_path=PROJECT_ROOT / "workflows" / "anima-2.9b-img2img-colorize.json",
        workflow_loader=loader,
        transport=transport,
    )
    assets = InferenceAssets(
        image_bytes=png_bytes(),
        character_references={"ignored": png_bytes((8, 8))},
    )
    output_path = tmp_path / "anima.webp"

    outcome = strategy.process(
        assets,
        output_path,
        SimpleNamespace(mode="anima_2_9b"),
    )

    assert outcome.reference_applied is False
    assert outcome.model_profile == "anima-2.9b-preview-v1"
    assert transport.calls[1]["input_images"].keys() == {"INPUT_IMAGE"}
    assert transport.calls[1]["prepare_workflow"] is None
    assert "realcugan-2x" not in transport.calls[1]["output_prefix"]
    assert ANIMA_2_9B_PROCESSING_REVISION in strategy.cache_revision(
        SimpleNamespace(mode="anima_2_9b"), assets
    )
    with Image.open(output_path) as output:
        assert output.size == (32, 48)


# 方法说明：验证加载器未注册专用能力时策略不会误用其他工作流。
def test_strategy_requires_dedicated_loader_capability(tmp_path):
    loader = FakeWorkflowLoader()
    loader.supports_anima_2_9b = None
    strategy = Anima29BModeStrategy(
        enabled=True,
        workflow_path=PROJECT_ROOT / "workflows" / "anima-2.9b-img2img-colorize.json",
        workflow_loader=loader,
        transport=FakeTransport(),
    )

    assert strategy.available() is False
    with pytest.raises(RuntimeError, match="服务未就绪"):
        strategy.process(
            InferenceAssets(image_bytes=png_bytes()),
            tmp_path / "unused.webp",
            SimpleNamespace(mode="anima_2_9b"),
        )
