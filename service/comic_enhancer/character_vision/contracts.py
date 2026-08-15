from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


RegionPart = Literal[
    "hair",
    "left_eye",
    "right_eye",
    "eyebrow",
    "mouth",
    "face_marking",
    "skin",
    "upper_clothing",
    "lower_clothing",
    "inner_clothing",
    "outer_clothing",
    "headwear",
    "hair_accessory",
    "neckwear",
    "gloves",
    "belt",
    "legwear",
    "footwear",
    "jewelry",
    "accessory",
    "prop",
]


class StrictVisionModel(BaseModel):
    """拒绝视觉模型返回契约外字段。"""

    model_config = ConfigDict(extra="forbid")


class ProfileRegion(StrictVisionModel):
    """记录参考图中可确定性采色的语义区域。"""

    part: RegionPart
    box_2d: tuple[int, int, int, int]
    confidence: float = Field(ge=0, le=1)
    structural_trait: str = Field(default="", max_length=120)

    @field_validator("box_2d")
    @classmethod
    def validate_box(cls, value: tuple[int, int, int, int]):
        """校验千分比坐标范围及方向。"""
        x1, y1, x2, y2 = value
        if not all(0 <= item <= 1000 for item in value):
            raise ValueError("box_2d 坐标必须位于 0 到 1000")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box_2d 必须是正面积矩形")
        return value


class CharacterProfileAnalysis(StrictVisionModel):
    """保存 VLM 对单张角色参考图的受控结构分析。"""

    character_id: str = Field(min_length=1, max_length=160)
    stable_traits: list[str] = Field(default_factory=list, max_length=8)
    outfit_traits: list[str] = Field(default_factory=list, max_length=8)
    regions: list[ProfileRegion] = Field(default_factory=list, max_length=24)

    @field_validator("stable_traits", "outfit_traits")
    @classmethod
    def validate_traits(cls, values: list[str]):
        """清理并限制会进入生成提示词的结构特征。"""
        normalized = [value.strip() for value in values if value.strip()]
        if any(len(value) > 120 for value in normalized):
            raise ValueError("角色结构特征过长")
        return list(dict.fromkeys(normalized))


class PageCharacterInstance(StrictVisionModel):
    """记录漫画页中一个已匹配角色实例。"""

    panel_id: int = Field(ge=1, le=200)
    box_2d: tuple[int, int, int, int]
    confidence: float = Field(ge=0, le=1)
    match_evidence: list[str] = Field(default_factory=list, max_length=6)
    counter_evidence: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("box_2d")
    @classmethod
    def validate_box(cls, value: tuple[int, int, int, int]):
        """校验漫画页实例的千分比矩形。"""
        x1, y1, x2, y2 = value
        if not all(0 <= item <= 1000 for item in value):
            raise ValueError("box_2d 坐标必须位于 0 到 1000")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box_2d 必须是正面积矩形")
        return value

    @field_validator("match_evidence", "counter_evidence")
    @classmethod
    def validate_evidence(cls, values: list[str]):
        """限制身份匹配证据为短语列表。"""
        normalized = [value.strip() for value in values if value.strip()]
        if any(len(value) > 300 for value in normalized):
            raise ValueError("身份匹配证据过长")
        return normalized


class PageCharacterMatch(StrictVisionModel):
    """记录候选角色在漫画页中的身份匹配结果。"""

    character_id: str = Field(min_length=1, max_length=160)
    reference_slot: int = Field(ge=1, le=3)
    visible: bool
    outfit_matches_reference: bool = False
    instances: list[PageCharacterInstance] = Field(default_factory=list, max_length=24)


class UnmatchedPerson(StrictVisionModel):
    """记录页面中未被强制绑定到候选角色的人物。"""

    panel_id: int = Field(ge=1, le=200)
    box_2d: tuple[int, int, int, int]
    reason: str = Field(default="", max_length=160)

    @field_validator("box_2d")
    @classmethod
    def validate_box(cls, value: tuple[int, int, int, int]):
        """校验未匹配人物的千分比矩形。"""
        x1, y1, x2, y2 = value
        if not all(0 <= item <= 1000 for item in value):
            raise ValueError("box_2d 坐标必须位于 0 到 1000")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("box_2d 必须是正面积矩形")
        return value


class CharacterPageAnalysis(StrictVisionModel):
    """保存一页漫画的角色身份与区域计划。"""

    characters: list[PageCharacterMatch] = Field(default_factory=list, max_length=3)
    unmatched_people: list[UnmatchedPerson] = Field(default_factory=list, max_length=32)


class CharacterVisionAnalyzer(ABC):
    """定义可替换的角色视觉分析 sidecar 契约。"""

    @property
    @abstractmethod
    def model_revision(self) -> str:
        """返回会影响角色档案和页面分析缓存的模型版本。"""
        raise NotImplementedError

    @abstractmethod
    def ready(self) -> bool:
        """检查 sidecar 健康状态和模型标识。"""
        raise NotImplementedError

    @abstractmethod
    def analyze_profile(
        self,
        *,
        character_id: str,
        display_name: str,
        image_bytes: bytes,
    ) -> CharacterProfileAnalysis:
        """从单张已确认角色参考图提取结构和采色区域。"""
        raise NotImplementedError

    @abstractmethod
    def analyze_page(
        self,
        *,
        image_bytes: bytes,
        candidates: list[dict[str, object]],
    ) -> CharacterPageAnalysis:
        """在当前漫画页中匹配候选角色并返回实例位置。"""
        raise NotImplementedError
