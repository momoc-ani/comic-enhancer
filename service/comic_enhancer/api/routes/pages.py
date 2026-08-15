from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ...domain import ProcessingMode, ProcessOptions, ProcessResult, WorkIdentity
from ..dependencies import authorize, get_context

router = APIRouter()

REFERENCE_MODES = {
    ProcessingMode.COBRA,
    ProcessingMode.FLUX2,
    ProcessingMode.FLUX2_QUANT,
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

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="empty image")
    if len(image_bytes) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image exceeds 30 MiB")

    if context.remote_adapters is not None:
        await context.remote_adapters.ensure(work, options)

    character_references: dict[str, bytes] = {}
    if options.mode in REFERENCE_MODES:
        resolution = await asyncio.to_thread(context.metadata.resolve, work)
        reference_limit = (
            context.settings.cobra_reference_limit
            if options.mode == ProcessingMode.COBRA
            else context.settings.flux2_reference_limit
        )
        entries = await context.reference_bank.build(resolution, work)
        for entry, reference in entries[:reference_limit]:
            character_references.setdefault(entry.character_id, reference)

    return await context.processor.process(
        image_bytes,
        None,
        work,
        options,
        character_references=character_references,
    )
