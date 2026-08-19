"""Comic Enhancer HTTP 传输层能力。"""

from .compression import (
    GZipRequestMiddleware,
    GZipResponseMiddleware,
    decompress_gzip,
    gzip_compress,
)

__all__ = [
    "GZipRequestMiddleware",
    "GZipResponseMiddleware",
    "decompress_gzip",
    "gzip_compress",
]
