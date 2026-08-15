from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions
from ...contracts import InferenceAssets, InferenceOutcome
from .preset import PresetModeStrategy


class QualityModeStrategy(PresetModeStrategy):
    """实现质量档的完整工作流选择、缓存和处理契约。"""

    mode = ProcessingMode.QUALITY

    # 方法说明：检查质量档所需的 ComfyUI 基础服务是否可用。
    def available(self) -> bool:
        return self._preset_available()

    # 方法说明：生成质量档工作流决定的缓存版本。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        return self._preset_cache_revision(options, assets)

    # 方法说明：执行质量档完整工作流并返回真实模型信息。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        return self._process_preset(assets, output_path, options)
