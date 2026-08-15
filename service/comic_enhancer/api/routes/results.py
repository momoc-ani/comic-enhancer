from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..dependencies import authorize, get_context

router = APIRouter()


@router.get("/v1/results/{filename}")
async def result(
    request: Request,
    filename: str,
    _: None = Depends(authorize),
) -> FileResponse:
    """鉴权后返回缓存中的增强结果图。"""
    safe_name = Path(filename).name
    result_root = get_context(request).settings.runtime_dir / "results"
    matches = list(result_root.glob(f"*/{safe_name}"))
    if not matches:
        raise HTTPException(status_code=404, detail="result not found")
    return FileResponse(matches[0], media_type="image/webp")
