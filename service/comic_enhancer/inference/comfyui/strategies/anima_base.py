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


ANIMA_BASE_PROCESSING_REVISION = "anima-base-v1-lineart-direct-v1"
ANIMA_BASE_STEPS = 30
ANIMA_BASE_CFG = 4.0
ANIMA_BASE_LLLITE_STRENGTH = 1.0


class AnimaBaseModeStrategy(ComfyUIModeStrategy):
    """实现 Anima Base + LLLite Lineart 的独立漫画上色实验档。"""

    mode = ProcessingMode.ANIMA_BASE
    output_prefix = "anima-base"

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
        supports = getattr(self.workflow_loader, "supports_anima_base", None)
        workflow_supported = bool(
            self.workflow_path
            and self.workflow_path.is_file()
            and callable(supports)
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
        return ":".join(
            [
                self.workflow_loader.revision(options),
                ANIMA_BASE_PROCESSING_REVISION,
                f"steps={ANIMA_BASE_STEPS}",
                f"cfg={ANIMA_BASE_CFG:g}",
                f"lllite_strength={ANIMA_BASE_LLLITE_STRENGTH:g}",
            ]
        )

    # 方法说明：执行单图 Anima Base 线稿控制上色并直出工作流结果。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        started = time.perf_counter()
        if not self.available():
            raise RuntimeError("Anima Base 服务未就绪")
        if self.workflow_path is None:
            raise RuntimeError("Anima Base 工作流未配置")

        loaded_workflow = self.workflow_loader.load(options)
        workflow_revision = self.workflow_loader.revision(options)
        log_operation(
            logger,
            logging.INFO,
            feature="Anima Base工作流加载",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "steps": ANIMA_BASE_STEPS,
                "cfg": ANIMA_BASE_CFG,
                "lllite_strength": ANIMA_BASE_LLLITE_STRENGTH,
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
                "Anima Base 工作流输出尺寸与原图不一致："
                f"expected={source_size}, actual={generated.size}"
            )
        save_output(generated, output_path)
        log_operation(
            logger,
            logging.INFO,
            feature="Anima Base服务端直出",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "output_scale": 1,
                "postprocess": "none",
            },
            result={
                "status": "success",
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


# 方法说明：读取原图经过 EXIF 方向校正后的准确宽高。
def _source_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file)
        return source.size


__all__ = [
    "ANIMA_BASE_CFG",
    "ANIMA_BASE_LLLITE_STRENGTH",
    "ANIMA_BASE_PROCESSING_REVISION",
    "ANIMA_BASE_STEPS",
    "AnimaBaseModeStrategy",
]
