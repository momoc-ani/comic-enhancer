from __future__ import annotations

from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions
from ...contracts import InferenceAssets, InferenceOutcome
from .base import reference_cache_revision
from .flux2_base import Flux2StrategyBase


FLUX2_9B_FAST_PROCESSING_REVISION = "flux2-klein-9b-fast-fp8-e4m3fn-v1"


class Flux29BFastModeStrategy(Flux2StrategyBase):
    """独立执行 FLUX.2 Klein 9B FP8 快速矩阵计算实验档。"""

    mode = ProcessingMode.FLUX2_9B_FAST
    output_prefix = "flux2-9b-fast"

    # 方法说明：检查 9B FP8 快速计算开关、专用工作流和 ComfyUI 是否可用。
    def available(self) -> bool:
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=self.workflow_loader.supports_flux2_9b_fast(),
        )

    # 方法说明：生成包含快速计算工作流和角色参考图的独立缓存版本。
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
        return f"{revision}:{FLUX2_9B_FAST_PROCESSING_REVISION}"

    # 方法说明：执行 9B FP8 快速计算工作流并输出原图尺寸的一阶段结果。
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
    "FLUX2_9B_FAST_PROCESSING_REVISION",
    "Flux29BFastModeStrategy",
]
