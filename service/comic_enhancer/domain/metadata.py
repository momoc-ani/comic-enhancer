from __future__ import annotations

from pydantic import BaseModel, Field

from .identity import CharacterReference


class WorkMetadata(BaseModel):
    provider: str
    provider_id: str
    title: str
    title_aliases: list[str] = Field(default_factory=list)
    author: str = ""
    summary: str = ""
    cover_url: str | None = None
    source_url: str | None = None
    characters: list[CharacterReference] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    fetched_at: str = ""


class MetadataResolution(BaseModel):
    work_key: str
    title: str
    selected: WorkMetadata | None = None
    candidates: list[WorkMetadata] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
