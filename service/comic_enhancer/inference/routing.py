from __future__ import annotations

from pathlib import Path

from ..domain import ProcessingMode, ProcessOptions, ResolvedAdapter
from .contracts import (
    AdapterPolicy,
    InferenceAssets,
    InferenceBackend,
    InferenceOutcome,
)
from .realcugan import RealCuganUpscaler


class RoutedInferenceBackend(InferenceBackend):
    """在主推理后端之外路由不依赖 ComfyUI 的独立处理档位。"""

    # 方法说明：组合主推理后端与平台原生放大实现。
    def __init__(
        self,
        backend: InferenceBackend,
        upscaler: RealCuganUpscaler,
    ):
        self.backend = backend
        self.upscaler = upscaler
        self.name = backend.name
        self.applies_adapters = backend.applies_adapters
        self.supported_base_models = backend.supported_base_models

    # 方法说明：返回当前实际可声明的模型档位。
    @property
    def model_profiles(self) -> tuple[str, ...]:
        profiles = list(self.backend.model_profiles)
        if self.upscale_profile_ready():
            profiles.append(self.upscaler.model_profile)
        return tuple(dict.fromkeys(profiles))

    # 方法说明：检查主推理后端是否已准备就绪。
    def ready(self) -> bool:
        return self.backend.ready()

    # 方法说明：检查 Cobra 模型档位是否可用。
    def cobra_profile_ready(self) -> bool:
        return self.backend.cobra_profile_ready()

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return self.backend.flux2_profile_ready()

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return self.backend.flux2_quant_profile_ready()

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return self.upscaler.available()

    # 方法说明：生成所选档位影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.cache_revision()
        revision = self.backend.cache_revision(options, resolved, assets)
        return revision if self.name == self.backend.name else f"{self.name}:{revision}"

    # 方法说明：返回独立放大档或主推理档对应的适配器策略。
    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        if options.mode == ProcessingMode.UPSCALE:
            return AdapterPolicy(
                enabled=False,
                compatible_base_models=frozenset(),
                required_workflow=None,
            )
        return self.backend.adapter_policy(assets, options)

    # 方法说明：将请求路由到 Real-CUGAN 或主推理后端。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.process(assets, output_path)
        return self.backend.process(assets, output_path, options, resolved)
