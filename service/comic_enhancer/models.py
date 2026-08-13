from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ProcessingMode(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class AdapterSource(StrEnum):
    WORK = "work"
    GENERIC = "generic"
    NONE = "none"


class WorkIdentity(BaseModel):
    source: str = Field(min_length=1)
    source_work_id: str = Field(min_length=1)
    title: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    cover_url: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_work_id}"


class ChapterIdentity(BaseModel):
    chapter_id: str = ""
    title: str = ""


class ProcessOptions(BaseModel):
    mode: ProcessingMode = ProcessingMode.FAST
    page_index: int = Field(default=0, ge=0)
    palette_version: str = "default"
    prefer_work_adapter: bool = True
    allow_generic_adapter: bool = True


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


class ResolvedAdapter(BaseModel):
    source: AdapterSource
    adapter: AdapterManifest | None
    reason: str


class ProcessResult(BaseModel):
    job_id: str
    cache_key: str
    work_key: str
    mode: ProcessingMode
    adapter_source: AdapterSource
    adapter_id: str | None
    adapter_applied: bool
    result_url: str
    elapsed_ms: int
    cached: bool


class Capabilities(BaseModel):
    service_version: str
    backend: str
    ready: bool
    adapter_policy: list[str]
    prefetch_pages: int
    max_parallel_inference: int
