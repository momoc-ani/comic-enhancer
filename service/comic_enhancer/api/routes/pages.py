from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ...application import process_page_with_references
from ...domain import ProcessingMode, ProcessOptions, ProcessResult, WorkIdentity
from ...logging_utils import log_operation
from ..dependencies import authorize, get_context

router = APIRouter()
logger = logging.getLogger(__name__)

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
    elif (
        options.mode == ProcessingMode.FLUX2_9B_LORA
        and not context.backend.flux2_9b_lora_profile_ready()
    ):
        unavailable_detail = "FLUX.2 Klein 9B LoRA 画质档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_4B_SOURCE
        and not context.backend.flux2_4b_source_profile_ready()
    ):
        unavailable_detail = "FLUX.2 Klein 4B 结构稳定档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_9B_FAST
        and not context.backend.flux2_9b_fast_profile_ready()
    ):
        unavailable_detail = "FLUX.2 Klein 9B FP8 快速档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_9B_FAST_LOWRES
        and not context.backend.flux2_9b_fast_lowres_profile_ready()
    ):
        unavailable_detail = "FLUX.2 Klein 9B FP8 低分辨率快速档未启用"
    elif (
        options.mode == ProcessingMode.FLUX2_4B_COLOR
        and not context.backend.flux2_4b_color_profile_ready()
    ):
        unavailable_detail = "FLUX.2 Klein 4B 色彩增强档未启用"
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

    try:
        result = await process_page_with_references(
            processor=context.processor,
            metadata=context.metadata,
            reference_bank=context.reference_bank,
            settings=context.settings,
            image_bytes=image_bytes,
            work=work,
            options=options,
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
