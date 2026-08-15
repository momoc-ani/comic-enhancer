from __future__ import annotations

import uuid
from pathlib import Path

from ....domain import ProcessOptions, ResolvedAdapter
from ...contracts import InferenceAssets, InferenceOutcome
from ..image_ops import protect_source_structure, save_output
from .base import ComfyUIModeStrategy


class PresetModeStrategy(ComfyUIModeStrategy):
    """仅提供快速档和质量档可显式调用的预设执行辅助。"""

    # 方法说明：检查 ComfyUI 基础服务是否可以执行预设工作流。
    def _preset_available(self) -> bool:
        return self.transport.ready()

    # 方法说明：使用调用档位的工作流内容生成缓存版本。
    def _preset_cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        return self.workflow_loader.revision(
            options,
            resolved,
            reference_available=False,
        )

    # 方法说明：执行调用档位的预设工作流并应用 SD1.5 结构保护。
    def _process_preset(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        loaded_workflow = self.workflow_loader.load(
            options,
            resolved,
            reference_available=False,
        )
        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images={"INPUT_IMAGE": assets.image_bytes},
            output_prefix=f"comic-enhancer/{uuid.uuid4().hex}",
        )
        save_output(
            protect_source_structure(assets.image_bytes, generated),
            output_path,
        )
        return InferenceOutcome(
            adapter_applied=loaded_workflow.adapter_applied,
            model_profile=loaded_workflow.model_profile,
        )
