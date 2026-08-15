from __future__ import annotations

from pydantic import BaseModel, Field


class WorkIdentity(BaseModel):
    source: str = Field(min_length=1)
    source_work_id: str = Field(min_length=1)
    title: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """生成作品身份的稳定键。"""
        return f"{self.source}:{self.source_work_id}"


class ChapterIdentity(BaseModel):
    chapter_id: str = ""
    title: str = ""


class CharacterReference(BaseModel):
    provider: str
    provider_id: str
    name: str
    summary: str = ""
    image_url: str | None = None
    relation: str = ""


class CharacterBankEntry(BaseModel):
    character_id: str
    name: str
    image_url: str
    provider: str = ""
    summary: str = ""
    portrait_reference_url: str | None = None
    full_body_reference_url: str | None = None
