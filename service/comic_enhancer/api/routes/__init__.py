"""导出 API 路由集合。"""

from .metadata import router as metadata_router
from .pages import router as pages_router
from .pregeneration import router as pregeneration_router
from .results import router as results_router
from .system import router as system_router

__all__ = [
    "metadata_router",
    "pages_router",
    "pregeneration_router",
    "results_router",
    "system_router",
]
