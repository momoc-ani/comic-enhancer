"""兼容 ASGI 启动入口，应用实现位于 api 包。"""

from .api.app import create_app
from .application.reference_bank import prioritized_metadata_candidates

app = create_app()

__all__ = ["app", "create_app", "prioritized_metadata_candidates"]
