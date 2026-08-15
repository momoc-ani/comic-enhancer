from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from ..models import ProcessingMode, ProcessOptions, ResolvedAdapter
from .contracts import InferenceAssets, InferenceBackend, InferenceOutcome


class PassthroughBackend(InferenceBackend):
    """在不捆绑模型权重时保留完整 API 行为。"""

    name = "passthrough"

    # 方法说明：保存原图并为质量档应用轻量本地图像增强。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(BytesIO(assets.image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if options.mode == ProcessingMode.QUALITY:
                image = ImageEnhance.Contrast(image).enhance(1.04)
                image = ImageEnhance.Sharpness(image).enhance(1.08)
            image.save(output_path, format="WEBP", quality=92, method=4)
        return InferenceOutcome(
            adapter_applied=False,
            model_profile="passthrough",
        )
