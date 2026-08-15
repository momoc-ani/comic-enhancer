from __future__ import annotations

import logging
from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions, ResolvedAdapter
from ...contracts import AdapterPolicy, InferenceAssets, InferenceOutcome
from .flux2_base import FLUX2_PROCESSING_REVISION, Flux2StrategyBase


logger = logging.getLogger(__name__)


class Flux2ModeStrategy(Flux2StrategyBase):
    """独立实现 FLUX.2 最高质量档及质量档回退。"""

    mode = ProcessingMode.FLUX2
    output_prefix = "flux2"

    # 方法说明：检查 FLUX.2 开关、工作流和 ComfyUI 服务是否可用。
    def available(self) -> bool:
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=self.workflow_loader.supports_flux2(),
        )

    # 方法说明：生成最高质量档独立的缓存版本。
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
            quantized=False,
        )

    # 方法说明：声明 FLUX.2 仅沿用质量档适配器解析规则。
    def adapter_policy(self) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=self.adapter_workflow,
        )

    # 方法说明：执行 FLUX.2，失败时显式回退质量档。
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
            logger.exception("FLUX.2 最高质量档失败，回退到质量工作流")
            return self._fallback_to_quality(assets, output_path, options, resolved)


__all__ = ["FLUX2_PROCESSING_REVISION", "Flux2ModeStrategy"]
