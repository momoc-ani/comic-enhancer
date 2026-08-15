from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import re
from typing import Any

from ..models import WorkIdentity, WorkMetadata


# 方法说明：将任意元数据值规范化为文本。
def text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return ""


# 方法说明：从复合元数据值中提取首个有效文本。
def first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            normalized = first_text(item)
            if normalized:
                return normalized
        return ""
    if isinstance(value, dict):
        for key in ("v", "name", "title", "label"):
            normalized = text(value.get(key))
            if normalized:
                return normalized
        return ""
    return text(value)


# 方法说明：从提供方图片数据中提取封面地址。
def cover(images: Any) -> str | None:
    if isinstance(images, dict):
        for key in ("large", "extraLarge", "original", "medium", "common", "small"):
            value = text(images.get(key))
            if value:
                return value
    return None


# 方法说明：计算元数据候选与作品身份的匹配置信度。
def confidence(title: str, work: WorkIdentity, *, author: str = "") -> float:
    normalized_title = text(title).casefold()
    normalized_work_title = text(work.title).casefold()
    if not normalized_title or not normalized_work_title:
        return 0.0
    score = 0.45
    if normalized_work_title == normalized_title:
        score += 0.35
    elif normalized_title in normalized_work_title or normalized_work_title in normalized_title:
        score += 0.2
    if author and work.author and author.casefold() in work.author.casefold():
        score += 0.15
    return min(score, 1.0)


# 方法说明：计算候选标题集合的最高匹配置信度。
def title_confidence(
    titles: list[str],
    work: WorkIdentity,
    *,
    author: str = "",
) -> float:
    return max(
        (confidence(title, work, author=author) for title in titles if title),
        default=0.0,
    )


# 方法说明：返回当前 UTC 时间的标准字符串。
def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetadataProvider(ABC):
    """定义单个外部元数据提供方的查询契约。"""

    name: str

    # 方法说明：初始化提供方的网络请求超时。
    def __init__(self, *, timeout_seconds: int = 8):
        self.timeout_seconds = timeout_seconds

    # 方法说明：查询并转换当前提供方的作品元数据。
    @abstractmethod
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        raise NotImplementedError


_text = text
_first_text = first_text
_cover = cover
_confidence = confidence
_title_confidence = title_confidence
_now = now
