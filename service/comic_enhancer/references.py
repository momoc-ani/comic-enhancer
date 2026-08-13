from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import ipaddress
import logging
from pathlib import Path
import socket
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageFilter, ImageOps, ImageStat


logger = logging.getLogger(__name__)
REFERENCE_SELECTION_REVISION = "reference-view-v2"


@dataclass(frozen=True)
class ReferenceImageQuality:
    width: int
    height: int
    saturation: float
    detail: float
    colorful: bool
    full_body: bool
    usable: bool


REFERENCE_PROVIDER_PRIORITY = {
    "bangumi": 60,
    "anilist": 50,
    "mal": 40,
    "kitsu": 30,
    "shikimori": 20,
    "mangaupdates": 10,
}


def assess_reference_image(image_bytes: bytes) -> ReferenceImageQuality:
    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        sample = image.copy()
        sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
        saturation = ImageStat.Stat(sample.convert("HSV")).mean[1]
        grayscale = sample.convert("L")
        contrast = ImageStat.Stat(grayscale).stddev[0]
        detail = ImageStat.Stat(grayscale.filter(ImageFilter.FIND_EDGES)).mean[0]
        full_body = _looks_like_full_body_reference(sample)
    return ReferenceImageQuality(
        width=width,
        height=height,
        saturation=saturation,
        detail=detail,
        colorful=saturation >= 8.0,
        full_body=full_body,
        usable=min(width, height) >= 128 and contrast >= 5.0,
    )


def _looks_like_full_body_reference(image: Image.Image) -> bool:
    """Detect a full-height character cutout on a mostly uniform background."""
    sample = image.copy()
    sample.thumbnail((256, 256), Image.Resampling.LANCZOS)
    quantized = sample.quantize(colors=32)
    width, height = quantized.size
    border_width = max(2, round(min(width, height) * 0.06))
    pixels = list(quantized.getdata())
    border = [
        pixels[y * width + x]
        for y in range(height)
        for x in range(width)
        if x < border_width
        or x >= width - border_width
        or y < border_width
        or y >= height - border_width
    ]
    background_index = max(set(border), key=border.count)
    border_uniformity = border.count(background_index) / len(border)
    if border_uniformity < 0.65:
        return False

    palette = quantized.getpalette()
    background = tuple(palette[background_index * 3 : background_index * 3 + 3])
    foreground = [
        max(abs(channel - background[index]) for index, channel in enumerate(pixel))
        >= 28
        for pixel in sample.getdata()
    ]
    foreground_count = sum(foreground)
    coverage = foreground_count / len(foreground)
    if not 0.12 <= coverage <= 0.75:
        return False
    foreground_rows = [
        y
        for y in range(height)
        if any(foreground[y * width : (y + 1) * width])
    ]
    vertical_extent = (foreground_rows[-1] - foreground_rows[0] + 1) / height
    return vertical_extent >= 0.80


def reference_quality_rank(
    quality: ReferenceImageQuality,
    *,
    confirmed_source: bool,
    provider: str,
) -> tuple[int, int, int, int, int, int, float, float, int]:
    """Prioritize transferable full-character color information."""
    return (
        int(quality.usable),
        int(confirmed_source),
        int(quality.colorful),
        int(quality.full_body),
        min(quality.width, quality.height),
        quality.width * quality.height,
        round(quality.saturation, 1),
        quality.detail,
        REFERENCE_PROVIDER_PRIORITY.get(provider, 0),
    )


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
