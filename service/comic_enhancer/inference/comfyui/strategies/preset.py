from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from ....domain import ProcessOptions
from ....logging_utils import log_operation
from ...contracts import InferenceAssets, InferenceOutcome
from ..image_ops import protect_source_structure, save_output
from .base import ComfyUIModeStrategy


logger = logging.getLogger(__name__)


class PresetModeStrategy(ComfyUIModeStrategy):
    """仅提供快速档和质量档可显式调用的预设执行辅助。"""

    # 方法说明：检查 ComfyUI 基础服务是否可以执行预设工作流。
    def _preset_available(self) -> bool:
        return self.transport.ready()

    # 方法说明：使用调用档位的工作流内容生成缓存版本。
    def _preset_cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        return self.workflow_loader.revision(options)

    # 方法说明：执行调用档位的预设工作流并应用 SD1.5 结构保护。
    def _process_preset(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        started = time.perf_counter()
        loaded_workflow = self.workflow_loader.load(options)
        workflow_revision = self.workflow_loader.revision(options)
        log_operation(
            logger,
            logging.INFO,
            feature="ComfyUI预设工作流加载",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "input_bytes": len(assets.image_bytes),
            },
            result={
                "status": "loaded",
                "workflow_revision": workflow_revision[:16],
            },
        )
        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images={"INPUT_IMAGE": assets.image_bytes},
            output_prefix=f"comic-enhancer/{uuid.uuid4().hex}",
        )
        processed = protect_source_structure(assets.image_bytes, generated)
        save_output(processed, output_path)
        log_operation(
            logger,
            logging.INFO,
            feature="ComfyUI预设服务端后处理",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
            },
            result={
                "status": "success",
                "model_profile": loaded_workflow.model_profile,
                "comfyui_size": list(generated.size),
                "output_size": list(processed.size),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return InferenceOutcome(model_profile=loaded_workflow.model_profile)
