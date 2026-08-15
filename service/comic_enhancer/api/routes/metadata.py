from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from ...domain import MetadataResolution, WorkIdentity
from ..dependencies import authorize, get_context

router = APIRouter()


@router.post("/v1/metadata/resolve", response_model=MetadataResolution)
async def resolve_metadata(
    request: Request,
    work_json: str = Form(),
    _: None = Depends(authorize),
) -> MetadataResolution:
    """解析并返回作品元数据。"""
    context = get_context(request)
    try:
        work = context.identities.enrich(
            WorkIdentity.model_validate(json.loads(work_json))
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return await asyncio.to_thread(context.metadata.resolve, work)
