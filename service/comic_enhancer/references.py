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


logger = logging.getLogger(__name__)


class ReferenceImageStore:
    def __init__(self, root: Path, *, timeout_seconds: int = 20):
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.root.mkdir(parents=True, exist_ok=True)

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

    def _download(self, url: str) -> bytes:
        current = url
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for _ in range(4):
                self._validate_public_url(current)
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

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("reference URL must use public HTTP(S)")
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(parsed.hostname, parsed.port)
            }
        except socket.gaierror as error:
            raise ValueError("reference host cannot be resolved") from error
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("reference URL resolves to a non-public address")

    @staticmethod
    def _normalize(image_bytes: bytes) -> bytes:
        output = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            image.save(output, format="PNG", optimize=True)
        return output.getvalue()
