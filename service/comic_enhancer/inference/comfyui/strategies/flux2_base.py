from __future__ import annotations

from pathlib import Path
import uuid

from ....domain import ProcessingMode, ProcessOptions, ResolvedAdapter
from ...contracts import InferenceAssets, InferenceOutcome
from ..image_ops import restore_geometry, save_output
from .base import (
    ComfyUIModeStrategy,
    reference_cache_revision,
    select_reference_images,
)
from .quality import QualityModeStrategy


FLUX2_PROCESSING_REVISION = "flux2-baseline-direct-prompt-v12"
FLUX2_OUTPUT_SCALE = 2


class Flux2StrategyBase(ComfyUIModeStrategy):
    """复用两个 FLUX.2 档位共有的参考图执行过程。"""

    adapter_workflow = "quality"
    output_prefix = "flux2"

    # 方法说明：初始化 FLUX.2 开关、工作流、参考图限制和质量回退策略。
    def __init__(
        self,
        *,
        enabled: bool,
        workflow_path: Path | None,
        reference_limit: int,
        quality_strategy: QualityModeStrategy,
        **options,
    ):
        super().__init__(**options)
        self.enabled = enabled
        self.workflow_path = workflow_path
        self.reference_limit = max(1, min(3, reference_limit))
        self.quality_strategy = quality_strategy

    # 方法说明：生成包含工作流、参考图和 FLUX.2 处理版本的缓存标识。
    def _flux2_cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
        *,
        quantized: bool,
    ) -> str:
        revision = reference_cache_revision(
            self.workflow_loader,
            options,
            resolved,
            assets,
        )
        suffix = f"{FLUX2_PROCESSING_REVISION}:quant" if quantized else FLUX2_PROCESSING_REVISION
        return f"{revision}:{suffix}"

    # 方法说明：执行当前 FLUX.2 工作流并只做两倍几何恢复。
    def _process_flux2(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if not self.available():
            raise RuntimeError("FLUX.2 服务未就绪")
        references = select_reference_images(assets, limit=self.reference_limit)
        if not references:
            raise RuntimeError("FLUX.2 需要至少一张角色参考图")
        if self.workflow_path is None:
            raise RuntimeError("FLUX.2 工作流未配置")
        loaded_workflow = self.workflow_loader.load(options, resolved)
        input_images = {
            "INPUT_IMAGE": assets.image_bytes,
            **{
                f"REFERENCE_IMAGE_{index}": references[
                    min(index - 1, len(references) - 1)
                ]
                for index in range(1, 4)
            },
        }
        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images=input_images,
            output_prefix=(
                f"comic-enhancer/{self.output_prefix}-{uuid.uuid4().hex}"
            ),
        )
        generated = restore_geometry(
            assets.image_bytes,
            generated,
            output_scale=FLUX2_OUTPUT_SCALE,
        )
        save_output(generated, output_path)
        return InferenceOutcome(
            adapter_applied=False,
            reference_applied=True,
            model_profile=loaded_workflow.model_profile,
        )

    # 方法说明：使用质量档处理实验档失败的输入。
    def _fallback_to_quality(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        quality_options = options.model_copy(update={"mode": ProcessingMode.QUALITY})
        return self.quality_strategy.process(
            assets,
            output_path,
            quality_options,
            resolved,
        )
