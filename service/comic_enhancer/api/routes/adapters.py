from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ...adapters import GiteeError
from ...application import RemoteAdapterService
from ...domain import AdapterManifest
from ..dependencies import authorize_admin, get_context

router = APIRouter()


def _remote_service(request: Request, action: str) -> RemoteAdapterService:
    """取得已启用的远端适配器服务。"""
    service = get_context(request).remote_adapters
    if service is None:
        raise HTTPException(status_code=409, detail=f"Gitee {action}未启用")
    return service


@router.post("/v1/adapters/sync")
async def sync_adapters(
    request: Request,
    _: None = Depends(authorize_admin),
) -> dict[str, object]:
    """处理管理端适配器索引同步请求。"""
    service = _remote_service(request, "同步")
    try:
        index = await service.sync()
    except (GiteeError, OSError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"ok": True, "work_count": len(index.get("works", {}))}


@router.post("/v1/adapters/download")
async def download_adapter(
    request: Request,
    adapter_json: str = Form(),
    _: None = Depends(authorize_admin),
) -> dict[str, object]:
    """下载并安装指定的远端适配器。"""
    service = _remote_service(request, "同步")
    try:
        manifest = AdapterManifest.model_validate(json.loads(adapter_json))
        target = await service.download(manifest)
    except (GiteeError, ValueError, json.JSONDecodeError, OSError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"ok": True, "adapter_id": manifest.adapter_id, "path": str(target)}


@router.post("/v1/adapters/publish")
async def publish_adapter(
    request: Request,
    adapter_json: str = Form(),
    adapter: UploadFile = File(),
    _: None = Depends(authorize_admin),
) -> dict[str, object]:
    """发布指定适配器并更新索引。"""
    service = _remote_service(request, "发布")
    if not adapter.filename or not adapter.filename.lower().endswith(".safetensors"):
        raise HTTPException(status_code=422, detail="只允许发布 .safetensors LoRA")
    temporary: Path | None = None
    try:
        manifest = AdapterManifest.model_validate(json.loads(adapter_json))
        with tempfile.NamedTemporaryFile(suffix=".safetensors", delete=False) as stream:
            temporary = Path(stream.name)
            while chunk := await adapter.read(1024 * 1024):
                stream.write(chunk)
        published = await service.publish(temporary, manifest)
    except (GiteeError, ValueError, json.JSONDecodeError, OSError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "adapter": published.model_dump(mode="json")}
