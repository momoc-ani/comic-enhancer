from .contracts import (
    InferenceAssets,
    InferenceBackend,
    InferenceOutcome,
)
from .factory import create_backend
from .passthrough import PassthroughBackend
from .realcugan import (
    REALCUGAN_MODEL_PROFILE,
    REALCUGAN_PROCESSING_REVISION,
    RealCuganUpscaler,
)
from .routing import RoutedInferenceBackend


__all__ = [
    "InferenceAssets",
    "InferenceBackend",
    "InferenceOutcome",
    "PassthroughBackend",
    "REALCUGAN_MODEL_PROFILE",
    "REALCUGAN_PROCESSING_REVISION",
    "RealCuganUpscaler",
    "RoutedInferenceBackend",
    "create_backend",
]
