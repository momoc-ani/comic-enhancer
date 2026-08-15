from __future__ import annotations

from io import BytesIO
import math

from PIL import Image, ImageFilter, ImageOps


class ImageEmbeddingExtractor:
    """生成无需额外模型依赖的灰度、边缘和颜色检索向量。"""

    revision = "pillow-character-view-v1"

    # 方法说明：提取适合近似视图召回的单位长度向量。
    def extract(self, image_bytes: bytes) -> tuple[float, ...]:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            gray = ImageOps.fit(image.convert("L"), (8, 8), method=Image.Resampling.LANCZOS)
            edges = gray.filter(ImageFilter.FIND_EDGES)
            values = [value / 255 for value in gray.getdata()]
            values.extend(value / 255 for value in edges.getdata())
            for channel in range(3):
                histogram = image.histogram()[channel * 256 : (channel + 1) * 256]
                total = max(1, sum(histogram))
                values.extend(
                    sum(histogram[index : index + 32]) / total
                    for index in range(0, 256, 32)
                )
        norm = math.sqrt(sum(value * value for value in values)) or 1
        return tuple(value / norm for value in values)


# 方法说明：计算两个单位或非单位向量的余弦相似度。
def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("向量维度必须一致且非空")
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left)) or 1
    right_norm = math.sqrt(sum(value * value for value in right)) or 1
    return numerator / (left_norm * right_norm)
