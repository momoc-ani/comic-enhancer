from __future__ import annotations

import asyncio
from io import BytesIO
import gzip

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.config import Settings
from comic_enhancer.main import create_app
from comic_enhancer.transport import (
    GZipResponseMiddleware,
    decompress_gzip,
    gzip_compress,
)


# 方法说明：生成固定边界的 multipart 请求，便于验证压缩前后字节契约。
def multipart_body(image_bytes: bytes, boundary: str = "comic-test") -> bytes:
    parts = [
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="page.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + image_bytes
        + b"\r\n",
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="work_json"\r\n\r\n'
            '{"source":"copy_manga","source_work_id":"transport"}\r\n'
        ).encode(),
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="options_json"\r\n\r\n'
            '{"mode":"fast","page_index":0}\r\n'
        ).encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts)


# 方法说明：生成可被服务端推理后端读取的最小 PNG 图片。
def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
    return stream.getvalue()


# 方法说明：验证 gzip 解压后的图片和 multipart 字节均与原始请求一致。
def test_gzip_round_trip_preserves_bytes():
    original = multipart_body(b"\x00\xff\x11image-bytes\x80")
    compressed = gzip_compress(original)

    assert compressed != original
    assert decompress_gzip(compressed, max_output_bytes=len(original)) == original


# 方法说明：验证压缩请求可被 FastAPI 路由透明解析并返回结果。
def test_api_accepts_gzip_multipart_request(tmp_path):
    settings = Settings(api_token="test-token", runtime_dir=tmp_path / "runtime")
    client = TestClient(create_app(settings))
    image = png_bytes()
    boundary = "comic-api-test"
    body = multipart_body(image, boundary)
    response = client.post(
        "/v1/pages/process",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Encoding": "gzip",
            "Accept-Encoding": "gzip",
        },
        content=gzip_compress(body),
    )

    assert response.status_code == 200
    assert response.json()["model_profile"] == "passthrough"
    assert response.headers.get("content-encoding") == "gzip"


# 方法说明：验证非法 gzip 请求在进入 multipart 解析前被拒绝。
def test_api_rejects_invalid_gzip_request(tmp_path):
    settings = Settings(api_token="test-token", runtime_dir=tmp_path / "runtime")
    client = TestClient(create_app(settings))
    response = client.post(
        "/v1/pages/process",
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "multipart/form-data; boundary=unused",
            "Content-Encoding": "gzip",
        },
        content=b"not-gzip",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid gzip request"


# 方法说明：验证解压大小限制可阻断高膨胀比请求。
def test_decompress_gzip_enforces_output_limit():
    with pytest.raises(ValueError, match="exceeds limit"):
        decompress_gzip(gzip.compress(b"x" * 1024), max_output_bytes=16)


# 方法说明：验证真实服务器支持 path-send 时，缓存图片仍能进入 gzip 响应。
def test_path_send_image_response_is_compressed(tmp_path):
    image_path = tmp_path / "result.webp"
    image_bytes = b"image-payload-" * 100
    image_path.write_bytes(image_bytes)
    sent: list[dict] = []

    async def app(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"image/webp"),
                    (b"content-length", str(len(image_bytes)).encode()),
                ],
            }
        )
        await send({"type": "http.response.pathsend", "path": str(image_path)})

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = GZipResponseMiddleware(app)
    asyncio.run(
        middleware(
            {"type": "http", "headers": [(b"accept-encoding", b"gzip")]},
            receive,
            send,
        )
    )

    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
    ]
    headers = dict(sent[0]["headers"])
    assert headers[b"content-encoding"] == b"gzip"
    assert gzip.decompress(sent[1]["body"]) == image_bytes


# 方法说明：验证图片 gzip 无收益时保留 FileResponse 的原始 path-send。
def test_path_send_image_skips_gzip_without_gain(tmp_path):
    image_path = tmp_path / "already-compressed.webp"
    image_bytes = bytes(range(256))
    image_path.write_bytes(image_bytes)
    sent: list[dict] = []

    async def app(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"image/webp"),
                    (b"content-length", str(len(image_bytes)).encode()),
                ],
            }
        )
        await send({"type": "http.response.pathsend", "path": str(image_path)})

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(
        GZipResponseMiddleware(app)(
            {"type": "http", "headers": [(b"accept-encoding", b"gzip")]},
            receive,
            send,
        )
    )

    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.pathsend",
    ]
    headers = dict(sent[0]["headers"])
    assert b"content-encoding" not in headers
    assert sent[1]["path"] == str(image_path)
