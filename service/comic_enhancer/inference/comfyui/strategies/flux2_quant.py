from __future__ import annotations

import logging
from pathlib import Path

from ....models import ProcessingMode, ProcessOptions, ResolvedAdapter
from ...contracts import AdapterPolicy, InferenceAssets, InferenceOutcome
from .flux2_base import Flux2StrategyBase


logger = logging.getLogger(__name__)


class Flux2QuantModeStrategy(Flux2StrategyBase):
    """独立实现 FLUX.2 Qwen3 量化实验档及质量档回退。"""

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
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        return self._flux2_cache_revision(
            options,
            resolved,
            assets,
            quantized=True,
        )

    # 方法说明：声明量化 FLUX.2 仅沿用质量档适配器解析规则。
    def adapter_policy(self) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=self.adapter_workflow,
        )

    # 方法说明：执行量化 FLUX.2，失败时显式回退质量档。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        try:
            self.transport.unload_cobra_worker()
            return self._process_flux2(assets, output_path, options, resolved)
        except Exception:
            logger.exception("FLUX.2 Qwen3 4B 量化实验档失败，回退到质量工作流")
            return self._fallback_to_quality(assets, output_path, options, resolved)
