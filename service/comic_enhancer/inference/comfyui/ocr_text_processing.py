from __future__ import annotations

from PIL import Image

from .image_ops import protect_source_text_regions
from .text_regions import OCRDetection, OCRTextRegionDetector
from .workflow_processing import (
    WorkflowImagePreparation,
    WorkflowImageProcessingOutcome,
)


OCR_TEXT_PROCESSING_REVISION = "rapidocr-det-only-text-stroke-protection-v3"
OCR_TEXT_PADDING = 4
OCR_TEXT_FEATHER_RADIUS = 1.5


class OCRTextProtectionProcessor:
    """用本机 OCR 检测框保护工作流输出中的原图文字。"""

    name = "ocr_text_protection"
    cache_revision = OCR_TEXT_PROCESSING_REVISION

    # 方法说明：注入可替换的文字检测器和文字融合参数。
    def __init__(
        self,
        *,
        detector: OCRTextRegionDetector | None = None,
        padding: int = OCR_TEXT_PADDING,
        feather_radius: float = OCR_TEXT_FEATHER_RADIUS,
    ) -> None:
        self.detector = detector or OCRTextRegionDetector()
        self.padding = padding
        self.feather_radius = feather_radius

    # 方法说明：在工作流执行前检测并缓存原图文字多边形。
    def prepare(self, source_bytes: bytes) -> WorkflowImagePreparation:
        detection = self.detector.detect(source_bytes)
        return WorkflowImagePreparation(
            payload=detection,
            metrics={
                "region_count": len(detection.regions),
                "cache_hit": detection.cache_hit,
                "initialized_now": detection.initialized_now,
                "initialization_ms": round(detection.initialization_ms),
                "inference_ms": round(detection.inference_ms),
            },
        )

    # 方法说明：在工作流输出中仅回贴检测到的原图文字区域。
    def process(
        self,
        source_bytes: bytes,
        generated: Image.Image,
        preparation: WorkflowImagePreparation,
    ) -> WorkflowImageProcessingOutcome:
        detection = preparation.payload
        if not isinstance(detection, OCRDetection):
            raise TypeError("OCR preparation payload is invalid")
        protected = protect_source_text_regions(
            source_bytes,
            generated,
            detection.regions,
            padding=self.padding,
            feather_radius=self.feather_radius,
        )
        return WorkflowImageProcessingOutcome(
            image=protected,
            status="success" if detection.regions else "no_text",
            metrics={
                "padding": self.padding,
                "feather_radius": self.feather_radius,
            },
        )


__all__ = [
    "OCR_TEXT_FEATHER_RADIUS",
    "OCR_TEXT_PADDING",
    "OCR_TEXT_PROCESSING_REVISION",
    "OCRTextProtectionProcessor",
]
