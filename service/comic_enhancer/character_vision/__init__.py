"""导出角色视觉分析契约与 Qwen3-VL 实现。"""

from .contracts import (
    CharacterPageAnalysis,
    CharacterProfileAnalysis,
    CharacterVisionAnalyzer,
    PageCharacterInstance,
    PageCharacterMatch,
    ProfileRegion,
)
from .llamacpp import LlamaCppCharacterVisionAnalyzer
from .prompts import (
    PAGE_TEMPLATE_REVISION,
    PROFILE_TEMPLATE_REVISION,
    PROMPT_PLANNER_REVISION,
    build_character_prompt,
    build_global_prompt,
    build_static_character_guide,
)

__all__ = [
    "CharacterPageAnalysis",
    "CharacterProfileAnalysis",
    "CharacterVisionAnalyzer",
    "LlamaCppCharacterVisionAnalyzer",
    "PAGE_TEMPLATE_REVISION",
    "PROFILE_TEMPLATE_REVISION",
    "PROMPT_PLANNER_REVISION",
    "PageCharacterInstance",
    "PageCharacterMatch",
    "ProfileRegion",
    "build_character_prompt",
    "build_global_prompt",
    "build_static_character_guide",
]
