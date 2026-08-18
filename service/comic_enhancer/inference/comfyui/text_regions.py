from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import perf_counter
from typing import Any, Callable


TextPoint = tuple[float, float]
TextPolygon = tuple[TextPoint, ...]


@dataclass(frozen=True)
class OCRDetection:
    """保存本机 OCR 检测框及运行指标。"""

    regions: tuple[TextPolygon, ...]
    cache_hit: bool
    initialized_now: bool
    initialization_ms: float
    inference_ms: float


class OCRTextRegionDetector:
    """延迟初始化并复用 RapidOCR 的本机文字检测模型。"""

    # 方法说明：初始化 OCR 引擎工厂和有界原图检测缓存。
    def __init__(
        self,
        *,
        cache_size: int = 128,
        engine_factory: Callable[[], Any] | None = None,
    ) -> None:
        if cache_size < 1:
            raise ValueError("cache_size must be at least 1")
        self._cache_size = cache_size
        self._engine_factory = engine_factory or _create_rapidocr_engine
        self._engine: Any | None = None
        self._cache: OrderedDict[str, tuple[TextPolygon, ...]] = OrderedDict()
        self._lock = Lock()

    # 方法说明：检测原图文字多边形，并按内容哈希复用最近的检测结果。
    def detect(self, image_bytes: bytes) -> OCRDetection:
        digest = sha256(image_bytes).hexdigest()
        with self._lock:
            cached = self._cache.get(digest)
            if cached is not None:
                self._cache.move_to_end(digest)
                return OCRDetection(
                    regions=cached,
                    cache_hit=True,
                    initialized_now=False,
                    initialization_ms=0.0,
                    inference_ms=0.0,
                )

            initialized_now = self._engine is None
            initialization_started = perf_counter()
            engine = self._get_engine()
            initialization_ms = (
                (perf_counter() - initialization_started) * 1000
                if initialized_now
                else 0.0
            )
            inference_started = perf_counter()
            raw_regions, _ = engine(
                image_bytes,
                use_det=True,
                use_cls=False,
                use_rec=False,
            )
            inference_ms = (perf_counter() - inference_started) * 1000
            regions = _normalize_text_regions(raw_regions)
            self._cache[digest] = regions
            self._cache.move_to_end(digest)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            return OCRDetection(
                regions=regions,
                cache_hit=False,
                initialized_now=initialized_now,
                initialization_ms=initialization_ms,
                inference_ms=inference_ms,
            )

    # 方法说明：首次检测时创建 OCR 引擎，后续检测复用同一实例。
    def _get_engine(self) -> Any:
        if self._engine is None:
            self._engine = self._engine_factory()
        return self._engine


# 方法说明：创建只在当前 API 进程中运行的 RapidOCR 引擎。
def _create_rapidocr_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


# 方法说明：校验并冻结 RapidOCR 返回的文字多边形坐标。
def _normalize_text_regions(raw_regions: Any) -> tuple[TextPolygon, ...]:
    normalized: list[TextPolygon] = []
    for raw_polygon in raw_regions or ():
        try:
            polygon = tuple(
                (float(raw_point[0]), float(raw_point[1]))
                for raw_point in raw_polygon
            )
        except (IndexError, TypeError, ValueError):
            continue
        if len(polygon) >= 3:
            normalized.append(polygon)
    return tuple(normalized)


__all__ = [
    "OCRDetection",
    "OCRTextRegionDetector",
    "TextPoint",
    "TextPolygon",
]
