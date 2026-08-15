from __future__ import annotations

from fastapi import Header, HTTPException, Request

from .context import ApplicationContext


def get_context(request: Request) -> ApplicationContext:
    """从 FastAPI 应用状态取得依赖上下文。"""
    return request.app.state.context


def authorize(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """校验普通 API 请求的 Bearer Token。"""
    settings = get_context(request).settings
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=401, detail="invalid API token")


def authorize_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """校验管理 API 请求的 Bearer Token。"""
    settings = get_context(request).settings
    if not settings.admin_token or authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(status_code=403, detail="admin token required")
