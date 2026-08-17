from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
import time
import uuid

from PIL import Image, ImageOps

from ....domain import ProcessingMode, ProcessOptions
from ....logging_utils import log_operation
from ...contracts import InferenceAssets, InferenceOutcome
from ..image_ops import save_output
from .base import ComfyUIModeStrategy


logger = logging.getLogger(__name__)


ANIMA_2_9B_PROCESSING_REVISION = "anima-2.9b-img2img-direct-v1"
ANIMA_2_9B_DENOISE = 0.35
ANIMA_2_9B_STEPS = 32
ANIMA_2_9B_CFG = 4.0


def _anima_mode() -> ProcessingMode | str:
    """读取主分支已注册的 Anima-2.9B 模式，兼容独立测试环境。"""
    return getattr(ProcessingMode, "ANIMA_2_9B", "anima_2_9b")


class Anima29BModeStrategy(ComfyUIModeStrategy):
    """实现不依赖角色上下文的 Anima-2.9B 图生图实验档。"""

    mode = _anima_mode()
    output_prefix = "anima-2.9b"

    # 方法说明：初始化实验开关和专用工作流路径。
    def __init__(
        self,
        *,
        enabled: bool = False,
        workflow_path: Path | None = None,
        **options,
    ):
        super().__init__(**options)
        self.enabled = enabled
        self.workflow_path = workflow_path

    # 方法说明：检查开关、工作流、加载器能力和 ComfyUI 服务是否可用。
    def available(self) -> bool:
        supports = getattr(self.workflow_loader, "supports_anima_2_9b", None)
        workflow_supported = bool(
            self.workflow_path
            and self.workflow_path.is_file()
            and supports is not None
            and supports()
        )
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=workflow_supported,
        )

    # 方法说明：生成包含工作流和固定采样契约的缓存版本。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        workflow_revision = self.workflow_loader.revision(options)
        return ":".join(
            [
                workflow_revision,
                ANIMA_2_9B_PROCESSING_REVISION,
                f"steps={ANIMA_2_9B_STEPS}",
                f"cfg={ANIMA_2_9B_CFG:g}",
                f"denoise={ANIMA_2_9B_DENOISE:g}",
            ]
        )

    # 方法说明：执行单图 Anima-2.9B 图生图并直出工作流结果。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        started = time.perf_counter()
        if not self.available():
            raise RuntimeError("Anima-2.9B 服务未就绪")
        if self.workflow_path is None:
            raise RuntimeError("Anima-2.9B 工作流未配置")

        loaded_workflow = self.workflow_loader.load(options)
        workflow_revision = self.workflow_loader.revision(options)
        log_operation(
            logger,
            logging.INFO,
            feature="Anima-2.9B工作流加载",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "steps": ANIMA_2_9B_STEPS,
                "cfg": ANIMA_2_9B_CFG,
                "denoise": ANIMA_2_9B_DENOISE,
            },
            result={
                "status": "loaded",
                "workflow_revision": workflow_revision[:16],
                "input_bytes": len(assets.image_bytes),
                "reference_count": 0,
            },
        )

        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images={"INPUT_IMAGE": assets.image_bytes},
            output_prefix=f"comic-enhancer/{self.output_prefix}-{uuid.uuid4().hex}",
        )
        source_size = _source_size(assets.image_bytes)
        if generated.size != source_size:
            raise RuntimeError(
                "Anima-2.9B 工作流输出尺寸与原图不一致："
                f"expected={source_size}, actual={generated.size}"
            )
        save_output(generated, output_path)
        log_operation(
            logger,
            logging.INFO,
            feature="Anima-2.9B服务端直出",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "output_scale": 1,
                "postprocess": "none",
            },
            result={
                "status": "success",
                "comfyui_size": list(generated.size),
                "output_size": list(generated.size),
                "model_profile": loaded_workflow.model_profile,
                "geometry_handler": "comfyui-workflow",
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return InferenceOutcome(
            reference_applied=False,
            model_profile=loaded_workflow.model_profile,
        )


def _source_size(image_bytes: bytes) -> tuple[int, int]:
    """读取原图经过 EXIF 方向校正后的准确宽高。"""
    with Image.open(BytesIO(image_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file)
        return source.size


__all__ = [
    "ANIMA_2_9B_CFG",
    "ANIMA_2_9B_DENOISE",
    "ANIMA_2_9B_PROCESSING_REVISION",
    "ANIMA_2_9B_STEPS",
    "Anima29BModeStrategy",
]
