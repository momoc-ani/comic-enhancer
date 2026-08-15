from __future__ import annotations

import logging
from pathlib import Path
import uuid

from ....domain import ProcessingMode, ProcessOptions, ResolvedAdapter
from ...contracts import AdapterPolicy, InferenceAssets, InferenceOutcome
from ..image_ops import protect_cobra_structure, restore_geometry, save_output
from .base import (
    ComfyUIModeStrategy,
    reference_cache_revision,
    select_reference_images,
)
from .quality import QualityModeStrategy


logger = logging.getLogger(__name__)


class CobraModeStrategy(ComfyUIModeStrategy):
    """独立实现 Cobra 多参考图档位及质量档回退。"""

    mode = ProcessingMode.COBRA
    adapter_workflow = "quality"

    # 方法说明：初始化 Cobra 开关、工作流、参考图限制和质量回退策略。
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
        self.reference_limit = max(1, min(12, reference_limit))
        self.quality_strategy = quality_strategy

    # 方法说明：检查 Cobra 开关、工作流和 ComfyUI 服务是否可用。
    def available(self) -> bool:
        return self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=self.workflow_loader.supports_cobra(),
        )

    # 方法说明：生成包含 Cobra 工作流与参考图内容的缓存版本。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        return reference_cache_revision(
            self.workflow_loader,
            options,
            resolved,
            assets,
        )

    # 方法说明：声明 Cobra 仅沿用质量档适配器解析规则。
    def adapter_policy(self) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=self.adapter_workflow,
        )

    # 方法说明：执行 Cobra，失败时显式改用质量档并返回真实模型信息。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        try:
            return self._process_cobra(assets, output_path, resolved)
        except Exception:
            logger.exception("Cobra 实验档失败，回退到质量工作流")
            quality_options = options.model_copy(update={"mode": ProcessingMode.QUALITY})
            return self.quality_strategy.process(
                assets,
                output_path,
                quality_options,
                resolved,
            )

    # 方法说明：绑定最多十二张参考图并执行 Cobra 完整工作流。
    def _process_cobra(
        self,
        assets: InferenceAssets,
        output_path: Path,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if not self.available():
            raise RuntimeError("Cobra 服务未就绪")
        references = select_reference_images(assets, limit=self.reference_limit)
        if not references:
            raise RuntimeError("Cobra 需要至少一张角色参考图")
        if self.workflow_path is None:
            raise RuntimeError("Cobra 工作流未配置")
        loaded_workflow = self.workflow_loader.load(
            ProcessOptions(mode=ProcessingMode.COBRA),
            resolved,
        )
        input_images = {
            "INPUT_IMAGE": assets.image_bytes,
            **{
                f"REFERENCE_IMAGE_{index}": references[
                    min(index - 1, len(references) - 1)
                ]
                for index in range(1, 13)
            },
        }
        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images=input_images,
            output_prefix=f"comic-enhancer/cobra-{uuid.uuid4().hex}",
            prepare_workflow=lambda workflow: self._set_reference_count(
                workflow,
                len(references),
            ),
        )
        generated = restore_geometry(assets.image_bytes, generated)
        save_output(
            protect_cobra_structure(assets.image_bytes, generated),
            output_path,
        )
        return InferenceOutcome(
            adapter_applied=False,
            reference_applied=True,
            model_profile=loaded_workflow.model_profile,
        )

    # 方法说明：校验唯一 Cobra 节点并写入实际参考图数量。
    @staticmethod
    def _set_reference_count(workflow: dict, reference_count: int) -> None:
        cobra_nodes = [
            node
            for node in workflow.values()
            if isinstance(node, dict) and node.get("class_type") == "CobraColorize"
        ]
        if len(cobra_nodes) != 1:
            raise RuntimeError(
                "Cobra workflow must contain exactly one CobraColorize node"
            )
        cobra_nodes[0].setdefault("inputs", {})["reference_count"] = reference_count
