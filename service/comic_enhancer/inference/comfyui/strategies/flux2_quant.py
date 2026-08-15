from __future__ import annotations

from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions
from ...contracts import InferenceAssets, InferenceOutcome
from .flux2_base import Flux2StrategyBase


class Flux2QuantModeStrategy(Flux2StrategyBase):
    """独立实现 FLUX.2 Qwen3 量化实验档。"""

    mode = ProcessingMode.FLUX2_QUANT
    output_prefix = "flux2-quant"

    # 方法说明：检查量化档开关、工作流和 ComfyUI 服务是否可用。
    def available(self) -> bool:
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=self.workflow_loader.supports_flux2_quant(),
        )

    # 方法说明：生成量化实验档独立的缓存版本。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        return self._flux2_cache_revision(
            options,
            assets,
            quantized=True,
        )

    # 方法说明：执行量化 FLUX.2 并将结果交给外层 UPSCALE 二阶段。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        return self._process_flux2(assets, output_path, options)
