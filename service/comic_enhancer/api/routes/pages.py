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
    ProcessingMode.FLUX2_CHARACTER_LINEART,
}
OPTIONAL_CHARACTER_REFERENCE_MODES = {
    ProcessingMode.FLUX2_CHARACTER,
    ProcessingMode.FLUX2_CHARACTER_LINEART,
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
    request_started = time.perf_counter()
    context = get_context(request)
    try:
        work = context.identities.enrich(
            WorkIdentity.model_validate(json.loads(work_json))
        )
        options = ProcessOptions.model_validate(json.loads(options_json))
        if options.mode == ProcessingMode.FLUX2_CHARACTER_LINEART:
            options = options.model_copy(update={"comfyui_direct_output": False})
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty image")
    if len(image_bytes) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image exceeds 30 MiB")

    request_parameters = {
        "work": {
            "source": work.source,
            "source_work_id": work.source_work_id,
            "title": work.title,
            "title_aliases": work.title_aliases,
            "author": work.author,
            "tags": work.tags,
            "external_ids": work.external_ids,
            "has_cover_url": bool(work.cover_url),
        },
        "options": options.model_dump(mode="json"),
        "image": {
            "filename": image.filename or "",
            "content_type": image.content_type or "",
            "bytes": len(image_bytes),
        },
    }
    log_operation(
        logger,
        logging.INFO,
        feature="页面处理接口请求",
        parameters=request_parameters,
        result={"status": "validated"},
    )

    unavailable_detail = ""
    if (
        options.mode == ProcessingMode.UPSCALE
        and not context.backend.upscale_profile_ready()
    ):
        unavailable_detail = "Real-CUGAN 放大档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2
        and not context.backend.flux2_profile_ready()
    ):
        unavailable_detail = "FLUX.2 二阶段放大档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_QUANT
        and not context.backend.flux2_quant_profile_ready()
    ):
        unavailable_detail = "FLUX.2 量化二阶段放大档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_CHARACTER
        and not context.backend.flux2_character_profile_ready()
    ):
        unavailable_detail = "Qwen3-VL 角色稳定档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_CHARACTER_LINEART
        and not context.backend.flux2_character_lineart_profile_ready()
    ):
        unavailable_detail = "角色线稿保真档未启用"
    if unavailable_detail:
        log_operation(
            logger,
            logging.WARNING,
            feature="页面处理接口返回",
            parameters={
                "work_key": work.key,
                "mode": str(options.mode),
                "page_index": options.page_index,
            },
            result={
                "status": "rejected",
                "status_code": 409,
                "detail": unavailable_detail,
            },
            elapsed_ms=(time.perf_counter() - request_started) * 1000,
        )
        raise HTTPException(status_code=409, detail=unavailable_detail)

    character_references: dict[str, bytes] = {}
    character_reference_assets: list[CharacterReferenceAsset] = []
    if options.mode in REFERENCE_MODES:
        reference_started = time.perf_counter()
        reference_limit = context.settings.flux2_reference_limit
        metadata_candidates = 0
        entries = []
        reference_fallback = False
        try:
            resolution = await asyncio.to_thread(context.metadata.resolve, work)
            entries = await context.reference_bank.build(resolution, work)
            metadata_candidates = len(resolution.candidates)
        except Exception as error:
            optional_references = options.mode in OPTIONAL_CHARACTER_REFERENCE_MODES
            log_operation(
                logger,
                logging.WARNING if optional_references else logging.ERROR,
                feature="页面角色参考图准备",
                parameters={
                    "work_key": work.key,
                    "mode": str(options.mode),
                    "reference_limit": reference_limit,
                },
                result={
                    "status": "fallback" if optional_references else "failed",
                    "fallback": "no_reference" if optional_references else "",
                    "error": type(error).__name__,
                },
                elapsed_ms=(time.perf_counter() - reference_started) * 1000,
            )
            if optional_references:
                entries = []
                reference_fallback = True
            else:
                log_operation(
                    logger,
                    logging.ERROR,
                    feature="页面处理接口返回",
                    parameters={
                        "work_key": work.key,
                        "mode": str(options.mode),
                        "page_index": options.page_index,
                    },
                    result={
                        "status": "failed",
                        "status_code": 500,
                        "stage": "character_references",
                        "error": type(error).__name__,
                    },
                    elapsed_ms=(time.perf_counter() - request_started) * 1000,
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
                "status": "fallback" if reference_fallback else "success",
                "metadata_candidates": metadata_candidates,
                "bank_entries": len(entries),
                "selected_characters": len(character_reference_assets),
                "fallback": (
                    "no_reference"
                    if options.mode in OPTIONAL_CHARACTER_REFERENCE_MODES
                    and not character_reference_assets
                    else ""
                ),
            },
            elapsed_ms=(time.perf_counter() - reference_started) * 1000,
        )

    try:
        result = await context.processor.process(
            image_bytes,
            None,
            work,
            options,
            character_references=character_references,
            character_reference_assets=tuple(character_reference_assets),
        )
    except Exception as error:
        log_operation(
            logger,
            logging.ERROR,
            feature="页面处理接口返回",
            parameters={
                "work_key": work.key,
                "mode": str(options.mode),
                "page_index": options.page_index,
            },
            result={
                "status": "failed",
                "status_code": 500,
                "stage": "processing",
                "error": type(error).__name__,
            },
            elapsed_ms=(time.perf_counter() - request_started) * 1000,
        )
        raise
    log_operation(
        logger,
        logging.INFO,
        feature="页面处理接口返回",
        parameters={
            "work_key": work.key,
            "mode": str(options.mode),
            "page_index": options.page_index,
        },
        result={
            "status": "success",
            "status_code": 200,
            "response": result.model_dump(mode="json"),
        },
        elapsed_ms=(time.perf_counter() - request_started) * 1000,
    )
    return result
