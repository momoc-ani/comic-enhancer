"""兼容 Real-CUGAN 旧导入路径。"""

from .inference.realcugan import (
    REALCUGAN_MODEL_PROFILE,
    REALCUGAN_PROCESSING_REVISION,
    RealCuganUpscaler,
)
from .inference.routing import RoutedInferenceBackend


__all__ = [
    "REALCUGAN_MODEL_PROFILE",
    "REALCUGAN_PROCESSING_REVISION",
    "RealCuganUpscaler",
    "RoutedInferenceBackend",
]
