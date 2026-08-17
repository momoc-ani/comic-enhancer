from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

class ProcessingMode(StrEnum):
    FAST = "fast"
    QUALITY = "quality"
    UPSCALE = "upscale"
    FLUX2 = "flux2"
    FLUX2_QUANT = "flux2_quant"
    FLUX2_CHARACTER = "flux2_character"
    FLUX2_CHARACTER_LINEART = "flux2_character_lineart"
    ANIMA_BASE = "anima_base"
    ANIMA_2_9B = "anima_2_9b"


class ProcessOptions(BaseModel):
    mode: ProcessingMode = ProcessingMode.FAST
    page_index: int = Field(default=0, ge=0)
    palette_version: str = "default"
    comfyui_direct_output: bool = False


class ProcessResult(BaseModel):
    job_id: str
    cache_key: str
    work_key: str
    mode: ProcessingMode
    reference_applied: bool = False
    processed_panels: int = 0
    model_profile: str = ""
    result_url: str
    elapsed_ms: int
    cached: bool
    comfyui_direct_output: bool = False


class ProcessingModeOption(BaseModel):
    value: ProcessingMode
    label: str
    prefetch_pages: int = Field(default=1, ge=0, le=20)


class Capabilities(BaseModel):
    service_version: str
    backend: str
    ready: bool
    model_profiles: list[str] = Field(default_factory=list)
    processing_modes: list[ProcessingMode] = Field(default_factory=list)
    mode_options: list[ProcessingModeOption] = Field(default_factory=list)
    upscale_available: bool = False
    flux2_available: bool = False
    flux2_quant_available: bool = False
    flux2_character_available: bool = False
    flux2_character_lineart_available: bool = False
    anima_base_available: bool = False
    anima_2_9b_available: bool = False
    prefetch_pages: int
    max_parallel_inference: int
