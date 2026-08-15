from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from ..character_vision import CharacterPageAnalysis


@dataclass(frozen=True)
class CharacterReferenceAsset:
    """保留角色参考图的身份、来源和原始字节。"""

    character_id: str
    display_name: str
    image_bytes: bytes
    provider: str = ""
    summary: str = ""

    @property
    def sha256(self) -> str:
        """计算参考图内容摘要。"""
        return hashlib.sha256(self.image_bytes).hexdigest()


class CharacterColorEvidence(BaseModel):
    """保存由本地像素采样确定的角色部件颜色。"""

    model_config = ConfigDict(extra="forbid")

    part: str
    rgb: tuple[int, int, int]
    confidence: float = Field(ge=0, le=1)
    source: str = "reference_pixel_sample"


class CharacterProfile(BaseModel):
    """保存角色稳定特征、服装档案与有证据的颜色。"""

    model_config = ConfigDict(extra="forbid")

    work_key: str
    character_id: str
    display_name: str
    provider: str = ""
    summary: str = ""
    reference_sha256: str
    stable_traits: list[str] = Field(default_factory=list)
    outfit_traits: list[str] = Field(default_factory=list)
    colors: list[CharacterColorEvidence] = Field(default_factory=list)

    @property
    def digest(self) -> str:
        """生成角色档案的稳定内容摘要。"""
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PreparedCharacter:
    """绑定角色档案、参考图和工作流槽位。"""

    slot: int
    reference: CharacterReferenceAsset
    profile: CharacterProfile


@dataclass(frozen=True)
class CharacterPromptContext:
    """保存静态角色颜色提示所需的档案和稳定摘要。"""

    characters: tuple[PreparedCharacter, ...]
    digest: str


@dataclass(frozen=True)
class CharacterPageContext:
    """保存一次角色档位执行所需的完整确定性计划。"""

    characters: tuple[PreparedCharacter, ...]
    page_analysis: CharacterPageAnalysis
    digest: str

    # 方法说明：按工作流槽位查找已准备角色。
    def by_slot(self, slot: int) -> PreparedCharacter:
        for character in self.characters:
            if character.slot == slot:
                return character
        raise KeyError(slot)
