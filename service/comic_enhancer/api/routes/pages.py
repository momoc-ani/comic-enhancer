from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ...character_library import CharacterReferenceAsset
from ...domain import ProcessingMode, ProcessOptions, ProcessResult, WorkIdentity
from ...logging_utils import log_operation
from ..dependencies import authorize, get_context

router = APIRouter()
logger = logging.getLogger(__name__)

REFERENCE_MODES = {
    ProcessingMode.FLUX2,
    ProcessingMode.FLUX2_QUANT,
    ProcessingMode.FLUX2_CHARACTER,
}


@router.post("/v1/pages/process", response_model=ProcessResult)
async def process_page(
    request: Request,
    image: UploadFile = File(),
    work_json: str = Form(),
    options_json: str = Form(default="{}"),
    _: None = Depends(authorize),
) -> ProcessResult:
    """处理单页图片并返回统一结果。"""
    context = get_context(request)
    try:
        work = context.identities.enrich(
            WorkIdentity.model_validate(json.loads(work_json))
        )
        options = ProcessOptions.model_validate(json.loads(options_json))
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if (
        options.mode == ProcessingMode.UPSCALE
        and not context.backend.upscale_profile_ready()
    ):
        raise HTTPException(status_code=409, detail="Real-CUGAN 放大档未启用")
    if (
        options.mode == ProcessingMode.FLUX2
        and not context.backend.flux2_profile_ready()
    ):
        raise HTTPException(status_code=409, detail="FLUX.2 二阶段放大档未启用")
    if (
        options.mode == ProcessingMode.FLUX2_QUANT
        and not context.backend.flux2_quant_profile_ready()
    ):
        raise HTTPException(status_code=409, detail="FLUX.2 量化二阶段放大档未启用")
    if (
        options.mode == ProcessingMode.FLUX2_CHARACTER
        and not context.backend.flux2_character_profile_ready()
    ):
        raise HTTPException(status_code=409, detail="Qwen3-VL 角色稳定档未启用")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty image")
    if len(image_bytes) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image exceeds 30 MiB")

    character_references: dict[str, bytes] = {}
    character_reference_assets: list[CharacterReferenceAsset] = []
    if options.mode in REFERENCE_MODES:
        reference_started = time.perf_counter()
        reference_limit = context.settings.flux2_reference_limit
        try:
            resolution = await asyncio.to_thread(context.metadata.resolve, work)
            entries = await context.reference_bank.build(resolution, work)
        except Exception as error:
            log_operation(
                logger,
                logging.ERROR,
                feature="页面角色参考图准备",
                parameters={
                    "work_key": work.key,
                    "mode": str(options.mode),
                    "reference_limit": reference_limit,
                },
                result={"status": "failed", "error": type(error).__name__},
                elapsed_ms=(time.perf_counter() - reference_started) * 1000,
            )
            raise
        for entry, reference in entries[:reference_limit]:
            if entry.character_id in character_references:
                continue
            character_references[entry.character_id] = reference
            character_reference_assets.append(
                CharacterReferenceAsset(
                    character_id=entry.character_id,
                    display_name=entry.name,
                    image_bytes=reference,
                    provider=entry.provider,
                    summary=entry.summary,
                )
            )
        log_operation(
            logger,
            logging.INFO,
            feature="页面角色参考图准备",
            parameters={
                "work_key": work.key,
                "mode": str(options.mode),
                "reference_limit": reference_limit,
            },
            result={
                "status": "success",
                "metadata_candidates": len(resolution.candidates),
                "bank_entries": len(entries),
                "selected_characters": len(character_reference_assets),
            },
            elapsed_ms=(time.perf_counter() - reference_started) * 1000,
        )

    return await context.processor.process(
        image_bytes,
        None,
        work,
        options,
        character_references=character_references,
        character_reference_assets=tuple(character_reference_assets),
    )
