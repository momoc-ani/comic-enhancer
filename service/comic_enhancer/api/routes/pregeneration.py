from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ...domain import (
    ChapterIdentity,
    PregenerationJob,
    ProcessingMode,
    ProcessOptions,
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
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not chapter.chapter_id:
        raise HTTPException(status_code=422, detail="chapter_id is required")
    _ensure_profile_available(context, options.mode)
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
    )
    log_operation(
        logger,
        logging.INFO,
        feature="章节预生成入队",
        parameters={
            "work_key": work.key,
            "chapter_id": chapter.chapter_id,
            "page_index": options.page_index,
            "page_count": page_count,
            "priority": priority,
            "bytes": len(image_bytes),
        },
        result={"status": job["status"], "job_id": job["job_id"]},
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    return _response(job)


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
