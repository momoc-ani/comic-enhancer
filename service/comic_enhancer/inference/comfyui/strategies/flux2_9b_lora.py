from __future__ import annotations

from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions
from ...contracts import InferenceAssets, InferenceOutcome
from .base import reference_cache_revision
from .flux2_base import Flux2StrategyBase


FLUX2_9B_LORA_PROCESSING_REVISION = "flux2-klein-9b-lora-4step-v1"


class Flux29BLoraModeStrategy(Flux2StrategyBase):
    """独立执行 FLUX.2 Klein 9B 一致性 LoRA 画质档。"""

    mode = ProcessingMode.FLUX2_9B_LORA
    output_prefix = "flux2-9b-lora"

    # 方法说明：检查 9B LoRA 开关、专用工作流和 ComfyUI 是否可用。
    def available(self) -> bool:
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=self.workflow_loader.supports_flux2_9b_lora(),
        )

    # 方法说明：生成包含工作流和角色参考图的 9B 独立缓存版本。
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
        return f"{revision}:{FLUX2_9B_LORA_PROCESSING_REVISION}"

    # 方法说明：执行 9B LoRA 工作流并输出原图尺寸的一阶段结果。
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
    "FLUX2_9B_LORA_PROCESSING_REVISION",
    "Flux29BLoraModeStrategy",
]
