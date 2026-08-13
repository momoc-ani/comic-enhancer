from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ProcessingMode(StrEnum):
    FAST = "fast"
    QUALITY = "quality"
    MANGANINJA = "manganinja"


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
    external_ids: dict[str, str] = Field(default_factory=dict)

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
    workflows: dict[str, str] = Field(default_factory=dict)


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
    reference_applied: bool = False
    processed_panels: int = 0
    model_profile: str = ""
    result_url: str
    elapsed_ms: int
    cached: bool


class CharacterReference(BaseModel):
    provider: str
    provider_id: str
    name: str
    summary: str = ""
    image_url: str | None = None
    relation: str = ""


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


class Capabilities(BaseModel):
    service_version: str
    backend: str
    ready: bool
    adapter_policy: list[str]
    model_profiles: list[str] = Field(default_factory=list)
    processing_modes: list[ProcessingMode] = Field(default_factory=list)
    manganinja_available: bool = False
    prefetch_pages: int
    max_parallel_inference: int


class BoundingBox(BaseModel):
    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(gt=0)
    y2: int = Field(gt=0)

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)


class CharacterMatch(BaseModel):
    character_id: str | None = None
    character_name: str = ""
    reference_url: str | None = None
    portrait_reference_url: str | None = None
    full_body_reference_url: str | None = None
    status: str = "rejected"
    confidence: float = Field(default=0, ge=0, le=1)
    best_distance: float | None = Field(default=None, ge=0)
    second_distance: float | None = Field(default=None, ge=0)
    margin: float | None = Field(default=None, ge=0)
    reason: str = ""


class CharacterMask(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    counts: list[int] = Field(min_length=1)
    score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_counts(self):
        if any(count < 0 for count in self.counts):
            raise ValueError("character mask counts must be non-negative")
        if sum(self.counts) != self.width * self.height:
            raise ValueError("character mask counts do not match dimensions")
        return self


class CharacterInstance(BaseModel):
    instance_id: str
    cluster_id: str
    box: BoundingBox
    panel_index: int | None = Field(default=None, ge=0)
    match: CharacterMatch = Field(default_factory=CharacterMatch)
    mask: CharacterMask | None = None


class PanelRegion(BaseModel):
    panel_index: int = Field(ge=0)
    box: BoundingBox
    character_instance_ids: list[str] = Field(default_factory=list)


class PageAnalysis(BaseModel):
    image_hash: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    analyzer_profile: str
    panels: list[PanelRegion] = Field(default_factory=list)
    characters: list[CharacterInstance] = Field(default_factory=list)


class CharacterBankEntry(BaseModel):
    character_id: str
    name: str
    image_url: str
    provider: str = ""
    portrait_reference_url: str | None = None
    full_body_reference_url: str | None = None


class ChapterAnalysisResult(BaseModel):
    analyzer_profile: str
    pages: list[PageAnalysis] = Field(default_factory=list)
