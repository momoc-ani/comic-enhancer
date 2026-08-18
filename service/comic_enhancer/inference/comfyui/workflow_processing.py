from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image


@dataclass(frozen=True)
class WorkflowImagePreparation:
    """保存工作流执行前完成的图像分析状态。"""

    payload: Any
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowImageProcessingOutcome:
    """保存工作流执行后的图像加工结果。"""

    image: Image.Image
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)


class WorkflowImageProcessor(Protocol):
    """约束可插拔的工作流执行前后图像加工策略。"""

    name: str
    cache_revision: str

    # 方法说明：在工作流执行前分析原图并返回后处理所需状态。
    def prepare(self, source_bytes: bytes) -> WorkflowImagePreparation:
        ...

    # 方法说明：在工作流执行后使用准备状态加工生成图。
    def process(
        self,
        source_bytes: bytes,
        generated: Image.Image,
        preparation: WorkflowImagePreparation,
    ) -> WorkflowImageProcessingOutcome:
        ...


__all__ = [
    "WorkflowImagePreparation",
    "WorkflowImageProcessingOutcome",
    "WorkflowImageProcessor",
]
