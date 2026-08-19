from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ...domain import (
    ChapterIdentity,
    PregenerationJob,
    ProcessingMode,
    ProcessOptions,
    ProcessResult,
    SourceCacheResult,
    WorkIdentity,
)
from ...logging_utils import log_operation
from ..dependencies import authorize, get_context


router = APIRouter()
logger = logging.getLogger(__name__)

PROFILE_READINESS = {
    ProcessingMode.UPSCALE: "upscale_profile_ready",
    ProcessingMode.FLUX2: "flux2_profile_ready",
    ProcessingMode.FLUX2_QUANT: "flux2_quant_profile_ready",
    ProcessingMode.FLUX2_CHARACTER: "flux2_character_profile_ready",
    ProcessingMode.FLUX2_CHARACTER_LINEART: "flux2_character_lineart_profile_ready",
    ProcessingMode.FLUX2_9B_LORA: "flux2_9b_lora_profile_ready",
    ProcessingMode.FLUX2_9B_FAST: "flux2_9b_fast_profile_ready",
    ProcessingMode.FLUX2_9B_FAST_LOWRES: "flux2_9b_fast_lowres_profile_ready",
    ProcessingMode.FLUX2_4B_SOURCE: "flux2_4b_source_profile_ready",
    ProcessingMode.FLUX2_4B_COLOR: "flux2_4b_color_profile_ready",
}


# 方法说明：将内部任务行裁剪成插件可安全消费的响应模型。
def _response(job: dict) -> PregenerationJob:
    return PregenerationJob(
        job_id=job["job_id"],
        work_key=job["work_key"],
        chapter_id=job["chapter_id"],
        page_index=int(job["page_index"]),
        page_count=int(job["page_count"]),
        priority=int(job["priority"]),
        status=job["status"],
        attempts=int(job["attempts"]),
        cache_key=job.get("cache_key", ""),
        result_url=(
            f"/v1/results/{job['cache_key']}.webp" if job.get("cache_key") else ""
        ),
        error=job.get("error", ""),
    )


# 方法说明：在任务落盘前确认请求档位已经由当前后端启用。
def _ensure_profile_available(context, mode: ProcessingMode) -> None:
    method_name = PROFILE_READINESS.get(mode)
    if method_name and not getattr(context.backend, method_name)():
        raise HTTPException(status_code=409, detail=f"{mode} 档位未启用")


@router.post(
    "/v1/pregeneration/pages",
    response_model=PregenerationJob,
    status_code=202,
)
async def enqueue_page(
    request: Request,
    image: UploadFile = File(),
    work_json: str = Form(),
    chapter_json: str = Form(default="{}"),
    options_json: str = Form(default="{}"),
    page_count: int = Form(default=1),
    priority: int = Form(default=100),
    _: None = Depends(authorize),
) -> PregenerationJob:
    """上传单页原图并加入服务端可恢复的章节预生成队列。"""
    started = time.perf_counter()
    try:
        context = get_context(request)
        work = context.identities.enrich(WorkIdentity.model_validate(json.loads(work_json)))
        chapter = ChapterIdentity.model_validate(json.loads(chapter_json))
        options = ProcessOptions.model_validate(json.loads(options_json))
        if options.mode == ProcessingMode.FLUX2_CHARACTER_LINEART:
            options = options.model_copy(update={"comfyui_direct_output": False})
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not chapter.chapter_id:
        raise HTTPException(status_code=422, detail="chapter_id is required")
    _ensure_profile_available(context, options.mode)
    mode_revision = await asyncio.to_thread(
        context.processor.base_cache_revision,
        options,
    )
    if not 1 <= page_count <= 5000:
        raise HTTPException(status_code=422, detail="page_count must be between 1 and 5000")
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty image")
    if len(image_bytes) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image exceeds 30 MiB")
    job = await context.pregeneration.enqueue(
        work_key=work.key,
        work_json=work.model_dump(mode="json"),
        chapter_id=chapter.chapter_id,
        chapter_title=chapter.title,
        page_index=options.page_index,
        page_count=page_count,
        options_json=options.model_dump(mode="json"),
        priority=max(0, min(int(priority), 10000)),
        image_bytes=image_bytes,
        mode_revision=mode_revision,
    )
    log_operation(
        logger,
        logging.INFO,
        feature="章节预生成入队",
        parameters={
            "work_key": work.key,
            "work_title": work.title,
            "chapter_id": chapter.chapter_id,
            "chapter_title": chapter.title,
            "page_index": options.page_index,
            "page_number": options.page_index + 1,
            "page_count": page_count,
            "mode": options.mode.value,
            "priority": priority,
            "bytes": len(image_bytes),
        },
        result={"status": job["status"], "job_id": job["job_id"]},
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    return _response(job)


@router.post("/v1/pregeneration/cache/resolve", response_model=ProcessResult)
async def resolve_cache(
    request: Request,
    work_json: str = Form(),
    chapter_json: str = Form(),
    options_json: str = Form(default="{}"),
    _: None = Depends(authorize),
) -> ProcessResult:
    """按作品章节页码和处理档位返回已完成的持久化缓存。"""
    started = time.perf_counter()
    try:
        context = get_context(request)
        work = context.identities.enrich(WorkIdentity.model_validate(json.loads(work_json)))
        chapter = ChapterIdentity.model_validate(json.loads(chapter_json))
        options = ProcessOptions.model_validate(json.loads(options_json))
        if options.mode == ProcessingMode.FLUX2_CHARACTER_LINEART:
            options = options.model_copy(update={"comfyui_direct_output": False})
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    job = await asyncio.to_thread(
        context.pregeneration_store.resolve_completed,
        work.key,
        chapter.chapter_id,
        options.page_index,
        options.model_dump(mode="json"),
        context.cache,
        mode_revision=await asyncio.to_thread(
            context.processor.base_cache_revision,
            options,
        ),
    )
    if job is None:
        log_operation(
            logger,
            logging.INFO,
            feature="章节缓存查询",
            parameters={
                "work_key": work.key,
                "work_title": work.title,
                "chapter_id": chapter.chapter_id,
                "chapter_title": chapter.title,
                "page_index": options.page_index,
                "page_number": options.page_index + 1,
                "mode": options.mode.value,
            },
            result={"status": "miss"},
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        raise HTTPException(status_code=404, detail="chapter cache not found")
    metadata = context.cache.load_metadata(job["cache_key"])
    result = ProcessResult(
        job_id=job["job_id"],
        cache_key=job["cache_key"],
        work_key=work.key,
        mode=options.mode,
        reference_applied=bool(metadata.get("reference_applied", False)),
        processed_panels=int(metadata.get("processed_panels", 0)),
        model_profile=str(metadata.get("model_profile", "")),
        result_url=f"/v1/results/{job['cache_key']}.webp",
        elapsed_ms=0,
        cached=True,
        comfyui_direct_output=options.comfyui_direct_output,
    )
    log_operation(
        logger,
        logging.INFO,
        feature="章节缓存查询",
        parameters={
            "work_key": work.key,
            "work_title": work.title,
            "chapter_id": chapter.chapter_id,
            "chapter_title": chapter.title,
            "page_index": options.page_index,
            "page_number": options.page_index + 1,
            "mode": options.mode.value,
        },
        result={"status": "hit", "cache_key": job["cache_key"][:12]},
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    return result


@router.post(
    "/v1/pregeneration/source/resolve",
    response_model=SourceCacheResult,
)
async def resolve_source(
    request: Request,
    work_json: str = Form(),
    chapter_json: str = Form(),
    page_index: int = Form(ge=0),
    _: None = Depends(authorize),
) -> SourceCacheResult:
    """按作品、章节和页码返回可读取的本地原图缓存。"""
    started = time.perf_counter()
    try:
        context = get_context(request)
        work = context.identities.enrich(
            WorkIdentity.model_validate(json.loads(work_json))
        )
        chapter = ChapterIdentity.model_validate(json.loads(chapter_json))
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not chapter.chapter_id:
        raise HTTPException(status_code=422, detail="chapter_id is required")
    entry = await asyncio.to_thread(
        context.pregeneration_store.resolve_source,
        work.key,
        chapter.chapter_id,
        page_index,
    )
    parameters = {
        "work_key": work.key,
        "chapter_id": chapter.chapter_id,
        "page_index": page_index,
        "page_number": page_index + 1,
    }
    if entry is None:
        log_operation(
            logger,
            logging.INFO,
            feature="章节原图缓存查询",
            parameters=parameters,
            result={"status": "miss"},
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        raise HTTPException(status_code=404, detail="chapter source cache not found")
    result = SourceCacheResult(
        source_id=str(entry["source_id"]),
        work_key=work.key,
        chapter_id=chapter.chapter_id,
        page_index=page_index,
        source_sha256=str(entry["source_sha256"]),
        source_bytes=int(entry["source_bytes"]),
        media_type=str(entry["media_type"]),
        source_url=f"/v1/pregeneration/source/{entry['source_id']}",
    )
    log_operation(
        logger,
        logging.INFO,
        feature="章节原图缓存查询",
        parameters=parameters,
        result={
            "status": "hit",
            "source_sha256": result.source_sha256[:12],
            "bytes": result.source_bytes,
        },
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    return result


@router.get("/v1/pregeneration/source/{source_id}")
async def source_image(
    request: Request,
    source_id: str,
    _: None = Depends(authorize),
) -> FileResponse:
    """鉴权并返回完整性校验通过的本地章节原图。"""
    entry = await asyncio.to_thread(
        get_context(request).pregeneration_store.get_source,
        source_id,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="chapter source not found")
    return FileResponse(
        Path(str(entry["source_path"])),
        media_type=str(entry["media_type"]),
        filename=f"page-{int(entry['page_index']) + 1}.img",
    )


@router.get("/v1/pregeneration/jobs/{job_id}", response_model=PregenerationJob)
async def get_job(
    request: Request,
    job_id: str,
    _: None = Depends(authorize),
) -> PregenerationJob:
    """查询持久化预生成任务状态。"""
    job = get_context(request).pregeneration_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="pregeneration job not found")
    return _response(job)


@router.get("/v1/pregeneration/works/{work_key:path}", response_model=list[PregenerationJob])
async def list_work_jobs(
    request: Request,
    work_key: str,
    _: None = Depends(authorize),
) -> list[PregenerationJob]:
    """返回作品所有章节的持久化预生成状态。"""
    return [_response(job) for job in get_context(request).pregeneration_store.list_work(work_key)]
