"""兼容 ASGI 启动入口，应用实现位于 api 包。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


if __package__ in {None, ""}:
    # 直接执行文件时补充 service 包根目录，避免相对导入失去包上下文。
    service_root = Path(__file__).resolve().parents[1]
    if str(service_root) not in sys.path:
        sys.path.insert(0, str(service_root))
    from comic_enhancer.api.app import create_app
    from comic_enhancer.application.reference_bank import (
        prioritized_metadata_candidates,
    )
else:
    from .api.app import create_app
    from .application.reference_bank import prioritized_metadata_candidates

app = create_app()


def main(argv: list[str] | None = None) -> None:
    """解析本地启动参数并运行 Comic Enhancer API。"""
    parser = argparse.ArgumentParser(description="启动 Comic Enhancer API")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--log-level", default="info", help="Uvicorn 日志级别")
    options = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(
        app,
        host=options.host,
        port=options.port,
        log_level=options.log_level,
    )


if __name__ == "__main__":
    main()


__all__ = ["app", "create_app", "main", "prioritized_metadata_candidates"]
