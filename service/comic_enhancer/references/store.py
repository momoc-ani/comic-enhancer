from __future__ import annotations

from io import BytesIO
import hashlib
import ipaddress
import logging
from pathlib import Path
import socket
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageOps

from ..networking import external_http_client, resolve_external_proxy


logger = logging.getLogger(__name__)


class ReferenceImageStore:
    """校验、下载、规范化并缓存第三方角色参考图。"""

    # 方法说明：初始化参考图缓存目录和下载超时。
    def __init__(self, root: Path, *, timeout_seconds: int = 20):
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    # 方法说明：从本地缓存或远端获取规范化参考图。
    def get(self, url: str | None) -> bytes | None:
        if not url:
            return None
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.root / cache_key[:2] / f"{cache_key}.png"
        if path.is_file():
            return path.read_bytes()
        try:
            image_bytes = self._download(url)
            normalized = self._normalize(image_bytes)
        except (OSError, ValueError, httpx.HTTPError) as error:
            logger.warning("作品参考图获取失败 %s: %s", url, error)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(normalized)
        temporary.replace(path)
        return normalized

    # 方法说明：从经过校验的公网地址下载参考图。
    def _download(self, url: str) -> bytes:
        current = url
        for _ in range(4):
            decision = resolve_external_proxy(current)
            self._validate_public_url(current, proxied=decision.proxied)
            with external_http_client(
                current,
                decision=decision,
                timeout=self.timeout_seconds,
            ) as client:
                with client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": "ComicEnhancer/0.1"},
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("reference redirect has no location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    length = int(response.headers.get("content-length", "0") or 0)
                    if length > 20 * 1024 * 1024:
                        raise ValueError("reference image exceeds 20 MiB")
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 20 * 1024 * 1024:
                            raise ValueError("reference image exceeds 20 MiB")
                    return bytes(chunks)
        raise ValueError("reference image has too many redirects")

    # 方法说明：按代理路由拒绝可能访问内网或本机的参考图地址。
    @staticmethod
    def _validate_public_url(url: str, *, proxied: bool = False) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("reference URL must use public HTTP(S)")
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ValueError("reference URL resolves to a non-public address")
        try:
            literal_address = ipaddress.ip_address(hostname)
        except ValueError:
            literal_address = None
        if literal_address is not None:
            if not literal_address.is_global:
                raise ValueError("reference URL resolves to a non-public address")
            return
        if proxied:
            return
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, parsed.port)
            }
        except socket.gaierror as error:
            raise ValueError("reference host cannot be resolved") from error
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("reference URL resolves to a non-public address")

    # 方法说明：规范化参考图尺寸、色彩模式和编码。
    @staticmethod
    def _normalize(image_bytes: bytes) -> bytes:
        output = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            image.save(output, format="PNG", optimize=True)
        return output.getvalue()
