from .base import ComfyUIModeStrategy
from .anima_2_9b import (
    ANIMA_2_9B_PROCESSING_REVISION,
    Anima29BModeStrategy,
)
from .anima_base import (
    ANIMA_BASE_PROCESSING_REVISION,
    AnimaBaseModeStrategy,
)
from .fast import FastModeStrategy
from .flux2 import FLUX2_PROCESSING_REVISION, Flux2ModeStrategy
from .flux2_character import (
    FLUX2_CHARACTER_PROCESSING_REVISION,
    Flux2CharacterModeStrategy,
)
from .flux2_character_lineart import (
    FLUX2_CHARACTER_LINEART_PROCESSING_REVISION,
    Flux2CharacterLineartModeStrategy,
)
from .flux2_base import FLUX2_OUTPUT_SCALE
from .flux2_quant import Flux2QuantModeStrategy
from .preset import PresetModeStrategy
from .quality import QualityModeStrategy


__all__ = [
    "ComfyUIModeStrategy",
    "ANIMA_2_9B_PROCESSING_REVISION",
    "ANIMA_BASE_PROCESSING_REVISION",
    "FLUX2_PROCESSING_REVISION",
    "FLUX2_OUTPUT_SCALE",
    "FLUX2_CHARACTER_PROCESSING_REVISION",
    "FLUX2_CHARACTER_LINEART_PROCESSING_REVISION",
    "FastModeStrategy",
    "Anima29BModeStrategy",
    "AnimaBaseModeStrategy",
    "Flux2ModeStrategy",
    "Flux2CharacterModeStrategy",
    "Flux2CharacterLineartModeStrategy",
    "Flux2QuantModeStrategy",
    "PresetModeStrategy",
    "QualityModeStrategy",
]
