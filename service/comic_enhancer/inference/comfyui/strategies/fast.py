from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions, ResolvedAdapter
from ...contracts import AdapterPolicy, InferenceAssets, InferenceOutcome
from .preset import PresetModeStrategy


class FastModeStrategy(PresetModeStrategy):
    """实现快速档的完整工作流选择、缓存和处理契约。"""

    mode = ProcessingMode.FAST
    adapter_workflow = "fast"

    # 方法说明：检查快速档所需的 ComfyUI 基础服务是否可用。
    def available(self) -> bool:
        return self._preset_available()

    # 方法说明：生成快速档工作流与适配器共同决定的缓存版本。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        return self._preset_cache_revision(options, resolved, assets)

    # 方法说明：声明快速档允许使用 fast 适配器工作流。
    def adapter_policy(self) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=self.adapter_workflow,
        )

    # 方法说明：执行快速档完整工作流并返回真实模型信息。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        return self._process_preset(assets, output_path, options, resolved)
