from __future__ import annotations

from pathlib import Path

from ..domain import ProcessingMode, ProcessOptions
from .contracts import (
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

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return self.backend.flux2_profile_ready() and self.upscale_profile_ready()

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return self.backend.flux2_quant_profile_ready() and self.upscale_profile_ready()

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return self.upscaler.available()

    # 方法说明：生成所选档位影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None = None,
    ) -> str:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.cache_revision()
        revision = self.backend.cache_revision(options, assets)
        if options.mode in {ProcessingMode.FLUX2, ProcessingMode.FLUX2_QUANT}:
            return f"{revision}:post-upscale:{self.upscaler.cache_revision()}"
        return revision if self.name == self.backend.name else f"{self.name}:{revision}"

    # 方法说明：将请求路由到 Real-CUGAN 或主推理后端。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.process(assets, output_path)
        if options.mode in {ProcessingMode.FLUX2, ProcessingMode.FLUX2_QUANT}:
            return self._process_flux2_pipeline(assets, output_path, options)
        return self.backend.process(assets, output_path, options)

    # 方法说明：串联 FLUX.2 首阶段和 Real-CUGAN 二阶段放大策略。
    def _process_flux2_pipeline(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        if not self.upscale_profile_ready():
            raise RuntimeError("FLUX.2 二阶段放大资源未就绪")
        stage_path = output_path.with_name(f"{output_path.stem}.flux2-stage.webp")
        try:
            primary = self.backend.process(assets, stage_path, options)
            stage_bytes = stage_path.read_bytes()
            secondary = self.upscaler.process(
                InferenceAssets(image_bytes=stage_bytes),
                output_path,
            )
            return InferenceOutcome(
                reference_applied=primary.reference_applied,
                processed_panels=primary.processed_panels,
                model_profile=(
                    f"{primary.model_profile}+{secondary.model_profile}"
                ),
            )
        finally:
            stage_path.unlink(missing_ok=True)
