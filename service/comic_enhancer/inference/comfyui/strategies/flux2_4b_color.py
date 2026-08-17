from __future__ import annotations

from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions
from ...contracts import InferenceAssets, InferenceOutcome
from .base import reference_cache_revision
from .flux2_base import Flux2StrategyBase


FLUX2_4B_COLOR_PROCESSING_REVISION = "flux2-klein-4b-color-lora025-d065-4step-v1"


class Flux24BColorModeStrategy(Flux2StrategyBase):
    """独立执行 FLUX.2 Klein 4B 色彩增强档。"""

    mode = ProcessingMode.FLUX2_4B_COLOR
    output_prefix = "flux2-4b-color"

    # 方法说明：检查 4B 色彩增强开关、专用工作流和 ComfyUI 是否可用。
    def available(self) -> bool:
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=self.workflow_loader.supports_flux2_4b_color(),
        )

    # 方法说明：生成包含工作流和角色参考图的色彩增强缓存版本。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        revision = reference_cache_revision(
            self.workflow_loader,
            options,
            assets,
        )
        return f"{revision}:{FLUX2_4B_COLOR_PROCESSING_REVISION}"

    # 方法说明：执行 4B 色彩增强工作流并输出原图尺寸的一阶段结果。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        return self._process_flux2(
            assets,
            output_path,
            options,
            restore_source_output=True,
        )


__all__ = [
    "FLUX2_4B_COLOR_PROCESSING_REVISION",
    "Flux24BColorModeStrategy",
]
