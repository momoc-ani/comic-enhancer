"""提供与业务路由无关的 HTTP gzip 请求解压和字节压缩能力。"""

from __future__ import annotations

from io import BytesIO
import asyncio
import gzip
import json
from pathlib import Path
from typing import Awaitable, Callable

from starlette.datastructures import Headers, MutableHeaders


ASGIMessage = dict[str, object]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]


class PayloadTooLargeError(ValueError):
    """表示压缩或解压后的请求超过传输层限制。"""


# 方法说明：以 gzip 压缩原始字节并保持解压后的字节完全一致。
def gzip_compress(data: bytes, compresslevel: int = 6) -> bytes:
    """返回原始字节的 gzip 表示，不改变图片编码内容。"""
    return gzip.compress(data, compresslevel=compresslevel, mtime=0)


# 方法说明：分块解压 gzip 请求并限制解压后的最大字节数。
def decompress_gzip(data: bytes, max_output_bytes: int) -> bytes:
    """解压 gzip 字节；超过上限或格式错误时抛出传输层异常。"""
    output: list[bytes] = []
    output_bytes = 0
    try:
        with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as stream:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                output_bytes += len(chunk)
                if output_bytes > max_output_bytes:
                    raise PayloadTooLargeError("decompressed request exceeds limit")
                output.append(chunk)
    except PayloadTooLargeError:
        raise
    except (OSError, EOFError) as error:
        raise ValueError("invalid gzip request") from error
    return b"".join(output)


class GZipRequestMiddleware:
    """在 FastAPI 解析 multipart 前透明还原 gzip 请求体。"""

    def __init__(
        self,
        app,
        *,
        max_compressed_bytes: int = 35 * 1024 * 1024,
        max_decompressed_bytes: int = 40 * 1024 * 1024,
    ) -> None:
        """初始化请求体大小上限，避免压缩炸弹耗尽服务内存。"""
        self.app = app
        self.max_compressed_bytes = max_compressed_bytes
        self.max_decompressed_bytes = max_decompressed_bytes

    # 方法说明：读取请求体、解压 gzip 并把一次性字节重新交给下游 ASGI 应用。
    async def __call__(
        self,
        scope: dict[str, object],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        """处理 HTTP gzip 请求，其他协议和编码保持原样。"""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = _scope_headers(scope)
        encoding = headers.get("content-encoding", "").strip().lower()
        if encoding not in {"", "identity", "gzip"}:
            await _send_error(send, 415, "unsupported content encoding")
            return
        if encoding != "gzip":
            await self.app(scope, receive, send)
            return

        try:
            compressed = await _read_body(receive, self.max_compressed_bytes)
            decompressed = decompress_gzip(
                compressed,
                max_output_bytes=self.max_decompressed_bytes,
            )
        except PayloadTooLargeError:
            await _send_error(send, 413, "compressed request exceeds limit")
            return
        except ValueError:
            await _send_error(send, 400, "invalid gzip request")
            return

        _replace_request_headers(scope, len(decompressed))
        delivered = False

        # 方法说明：将解压后的完整请求体伪装成标准 ASGI 单段请求。
        async def receive_decompressed() -> ASGIMessage:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {
                "type": "http.request",
                "body": decompressed,
                "more_body": False,
            }

        await self.app(scope, receive_decompressed, send)


class GZipResponseMiddleware:
    """只压缩 JSON 和确有收益的图片文件响应。"""

    def __init__(self, app, *, minimum_size: int = 256, compresslevel: int = 6) -> None:
        """初始化响应文件大小和 gzip 压缩级别阈值。"""
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    # 方法说明：捕获 JSON 与 path-send 文件消息，在压缩有效时替换响应体。
    async def __call__(
        self,
        scope: dict[str, object],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        """只处理 HTTP gzip 响应，其余 ASGI 消息原样透传。"""
        if scope.get("type") != "http" or "gzip" not in _scope_headers(scope).get(
            "accept-encoding", ""
        ).lower():
            await self.app(scope, receive, send)
            return

        initial: ASGIMessage | None = None
        body_chunks: list[bytes] = []

        # 方法说明：拦截 FileResponse 的 path-send 消息并在必要时转换为压缩字节。
        async def send_response(message: ASGIMessage) -> None:
            nonlocal initial, body_chunks
            message_type = message.get("type")
            if message_type == "http.response.start":
                initial = message
                body_chunks = []
                return
            if initial is None:
                await send(message)
                return
            if message_type == "http.response.body":
                headers = Headers(raw=initial.get("headers", []))
                content_type = headers.get("content-type", "").lower()
                if not content_type.startswith("application/json"):
                    await send(initial)
                    initial = None
                    await send(message)
                    return
                body_chunks.append(bytes(message.get("body", b"")))
                if message.get("more_body", False):
                    return
                await _send_json_response(
                    initial,
                    b"".join(body_chunks),
                    send,
                    minimum_size=self.minimum_size,
                    compresslevel=self.compresslevel,
                )
                initial = None
                body_chunks = []
                return
            if message_type != "http.response.pathsend":
                await send(initial)
                initial = None
                await send(message)
                return

            headers = Headers(raw=initial.get("headers", []))
            if not headers.get("content-type", "").lower().startswith("image/"):
                await send(initial)
                initial = None
                await send(message)
                return

            path_value = message.get("path", b"")
            try:
                path_text = (
                    bytes(path_value).decode("utf-8")
                    if isinstance(path_value, bytes)
                    else str(path_value)
                )
                path = Path(path_text)
                original = await asyncio.to_thread(path.read_bytes)
                compressed = gzip_compress(original, compresslevel=self.compresslevel)
            except (OSError, UnicodeError):
                await send(initial)
                initial = None
                await send(message)
                return

            if len(original) < self.minimum_size or len(compressed) >= len(original):
                await send(initial)
                initial = None
                await send(message)
                return

            headers = MutableHeaders(raw=initial["headers"])
            headers.add_vary_header("Accept-Encoding")
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(compressed))
            await send(initial)
            initial = None
            await send({"type": "http.response.body", "body": compressed, "more_body": False})

        await self.app(scope, receive, send_response)


# 方法说明：按压缩后大小决定 JSON 是否使用 gzip，避免小响应反而变大。
async def _send_json_response(
    initial: ASGIMessage,
    body: bytes,
    send: ASGISend,
    *,
    minimum_size: int,
    compresslevel: int,
) -> None:
    """发送 JSON 响应；压缩无收益时保持原始字节。"""
    headers = Headers(raw=initial.get("headers", []))
    if (
        len(body) < minimum_size
        or headers.get("content-encoding")
        or int(initial.get("status", 200)) in {204, 304}
    ):
        await send(initial)
        await send({"type": "http.response.body", "body": body, "more_body": False})
        return

    compressed = gzip_compress(body, compresslevel=compresslevel)
    if len(compressed) >= len(body):
        await send(initial)
        await send({"type": "http.response.body", "body": body, "more_body": False})
        return

    response_headers = MutableHeaders(raw=initial["headers"])
    response_headers.add_vary_header("Accept-Encoding")
    response_headers["Content-Encoding"] = "gzip"
    response_headers["Content-Length"] = str(len(compressed))
    await send(initial)
    await send(
        {
            "type": "http.response.body",
            "body": compressed,
            "more_body": False,
        }
    )


# 方法说明：读取 ASGI 请求体并限制压缩字节总量。
async def _read_body(receive: ASGIReceive, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return b""
        if message.get("type") != "http.request":
            continue
        chunk = bytes(message.get("body", b""))
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError("compressed request exceeds limit")
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


# 方法说明：读取 ASGI scope 头部并规范化为可修改的字典。
def _scope_headers(scope: dict[str, object]) -> dict[str, str]:
    raw_headers = scope.get("headers", [])
    return {
        bytes(name).decode("latin-1").lower(): bytes(value).decode("latin-1")
        for name, value in raw_headers
    }


# 方法说明：更新下游看到的请求长度和编码，保持 multipart 边界头不变。
def _replace_request_headers(scope: dict[str, object], content_length: int) -> None:
    raw_headers = scope.get("headers", [])
    filtered = [
        (name, value)
        for name, value in raw_headers
        if bytes(name).lower() not in {b"content-encoding", b"content-length"}
    ]
    filtered.append((b"content-length", str(content_length).encode("ascii")))
    scope["headers"] = filtered


# 方法说明：在传输层提前返回统一 JSON 错误，避免下游解析半截请求。
async def _send_error(send: ASGISend, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


__all__ = [
    "GZipRequestMiddleware",
    "GZipResponseMiddleware",
    "decompress_gzip",
    "gzip_compress",
]
