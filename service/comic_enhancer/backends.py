"""兼容旧导入路径；新代码应直接使用 comic_enhancer.inference。"""

from .inference import (
    AdapterPolicy,
    InferenceAssets,
    InferenceBackend,
    InferenceOutcome,
    PassthroughBackend,
    REALCUGAN_MODEL_PROFILE,
    REALCUGAN_PROCESSING_REVISION,
    RealCuganUpscaler,
    RoutedInferenceBackend,
    create_backend,
)
from .inference.comfyui import ComfyUIBackend
from .inference.comfyui.strategies import (
    ComfyUIModeStrategy,
    FLUX2_OUTPUT_SCALE,
    FLUX2_PROCESSING_REVISION,
    FastModeStrategy,
    Flux2ModeStrategy,
    Flux2QuantModeStrategy,
    PresetModeStrategy,
    QualityModeStrategy,
)


__all__ = [
    "AdapterPolicy",
    "ComfyUIBackend",
    "ComfyUIModeStrategy",
    "FLUX2_OUTPUT_SCALE",
    "FLUX2_PROCESSING_REVISION",
    "FastModeStrategy",
    "Flux2ModeStrategy",
    "Flux2QuantModeStrategy",
    "InferenceAssets",
    "InferenceBackend",
    "InferenceOutcome",
    "PassthroughBackend",
    "PresetModeStrategy",
    "QualityModeStrategy",
    "REALCUGAN_MODEL_PROFILE",
    "REALCUGAN_PROCESSING_REVISION",
    "RealCuganUpscaler",
    "RoutedInferenceBackend",
    "create_backend",
]
