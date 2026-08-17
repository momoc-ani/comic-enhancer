from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import __version__
from ...domain import Capabilities, ProcessingMode, ProcessingModeOption
from ..dependencies import authorize, get_context

router = APIRouter()

MODE_LABELS = {
    ProcessingMode.FAST: ("快速模式", 3),
    ProcessingMode.QUALITY: ("质量模式", 2),
    ProcessingMode.UPSCALE: ("放大模式（Real-CUGAN 2x）", 1),
    ProcessingMode.FLUX2: ("最高质量模式（FLUX.2）", 1),
    ProcessingMode.FLUX2_QUANT: ("质量模式（FLUX.2 量化实验）", 1),
    ProcessingMode.FLUX2_CHARACTER: ("角色稳定模式（Qwen3-VL + FLUX.2）", 1),
    ProcessingMode.FLUX2_CHARACTER_LINEART: (
        "角色线稿保真模式（Qwen3-VL + FLUX.2）",
        1,
    ),
    ProcessingMode.FLUX2_9B_LORA: ("FLUX.2 Klein 9B LoRA 画质模式", 1),
    ProcessingMode.FLUX2_4B_SOURCE: ("FLUX.2 Klein 4B 结构稳定模式", 1),
}


@router.get("/v1/health")
async def health(request: Request) -> dict[str, object]:
    """返回服务健康状态。"""
    backend = get_context(request).backend
    return {
        "ready": backend.ready(),
        "version": __version__,
        "backend": backend.name,
    }


@router.get("/v1/capabilities", response_model=Capabilities)
async def capabilities(
    request: Request,
    _: None = Depends(authorize),
) -> Capabilities:
    """返回后端能力、档位和预取配置。"""
    context = get_context(request)
    backend = context.backend
    settings = context.settings
    availability = {
        ProcessingMode.UPSCALE: backend.upscale_profile_ready(),
        ProcessingMode.FLUX2: backend.flux2_profile_ready(),
        ProcessingMode.FLUX2_QUANT: backend.flux2_quant_profile_ready(),
        ProcessingMode.FLUX2_CHARACTER: backend.flux2_character_profile_ready(),
        ProcessingMode.FLUX2_CHARACTER_LINEART: (
            backend.flux2_character_lineart_profile_ready()
        ),
        ProcessingMode.FLUX2_9B_LORA: backend.flux2_9b_lora_profile_ready(),
        ProcessingMode.FLUX2_4B_SOURCE: backend.flux2_4b_source_profile_ready(),
    }
    processing_modes = [
        mode
        for mode in ProcessingMode
        if mode not in availability or availability[mode]
    ]
    return Capabilities(
        service_version=__version__,
        backend=backend.name,
        ready=backend.ready(),
        model_profiles=list(backend.model_profiles),
        processing_modes=processing_modes,
        mode_options=[
            ProcessingModeOption(
                value=mode,
                label=MODE_LABELS[mode][0],
                prefetch_pages=MODE_LABELS[mode][1],
            )
            for mode in processing_modes
        ],
        upscale_available=availability[ProcessingMode.UPSCALE],
        flux2_available=availability[ProcessingMode.FLUX2],
        flux2_quant_available=availability[ProcessingMode.FLUX2_QUANT],
        flux2_character_available=availability[ProcessingMode.FLUX2_CHARACTER],
        flux2_character_lineart_available=availability[
            ProcessingMode.FLUX2_CHARACTER_LINEART
        ],
        flux2_9b_lora_available=availability[ProcessingMode.FLUX2_9B_LORA],
        flux2_4b_source_available=availability[ProcessingMode.FLUX2_4B_SOURCE],
        prefetch_pages=settings.prefetch_pages,
        max_parallel_inference=settings.max_parallel_inference,
    )
