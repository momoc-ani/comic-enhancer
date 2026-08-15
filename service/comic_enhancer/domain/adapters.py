from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AdapterSource(StrEnum):
    WORK = "work"
    GENERIC = "generic"
    NONE = "none"


class AdapterManifest(BaseModel):
    adapter_id: str
    name: str
    base_model: str
    revision: str
    file: str | None = None
    sha256: str | None = None
    recommended_weight: float = Field(default=0.45, ge=0, le=2)
    license: str = "unknown"
    enabled: bool = True
    work_key: str | None = None
    download_url: str | None = None
    release_id: int | None = None
    asset_id: int | None = None
    workflows: dict[str, str] = Field(default_factory=dict)


class ResolvedAdapter(BaseModel):
    source: AdapterSource
    adapter: AdapterManifest | None
    reason: str
