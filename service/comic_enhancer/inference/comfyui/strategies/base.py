from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
from pathlib import Path

from ....domain import ProcessingMode, ProcessOptions
from ...contracts import InferenceAssets, InferenceOutcome
from ..transport import ComfyUITransport
from ..workflows import WorkflowLoader


class ComfyUIModeStrategy(ABC):
    """定义单个 ComfyUI 处理档位必须实现的完整行为。"""

    mode: ProcessingMode

    # 方法说明：注入工作流加载器和 ComfyUI 传输层。
    def __init__(
        self,
        *,
        workflow_loader: WorkflowLoader,
        transport: ComfyUITransport,
    ):
        self.workflow_loader = workflow_loader
        self.transport = transport

    # 方法说明：检查当前处理档位是否可用。
    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    # 方法说明：生成当前档位影响推理缓存的版本标识。
    @abstractmethod
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        raise NotImplementedError

    # 方法说明：按当前档位处理输入并返回真实执行结果。
    @abstractmethod
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        raise NotImplementedError


# 方法说明：生成包含工作流和角色参考图内容的缓存版本。
def reference_cache_revision(
    workflow_loader: WorkflowLoader,
    options: ProcessOptions,
    assets: InferenceAssets | None,
) -> str:
    reference_hashes: list[str] = []
    if assets is not None:
        reference_hashes = sorted(
            hashlib.sha256(value).hexdigest()
            for value in (assets.character_references or {}).values()
        )
    workflow_revision = workflow_loader.revision(options)
    return ":".join([workflow_revision, *reference_hashes])


# 方法说明：按既定优先级去重并限制角色参考图片数量。
def select_reference_images(
    assets: InferenceAssets,
    *,
    limit: int,
) -> list[bytes]:
    candidates: list[bytes] = []
    if assets.reference_bytes is not None:
        candidates.append(assets.reference_bytes)
    candidates.extend((assets.character_references or {}).values())
    unique: list[bytes] = []
    seen: set[str] = set()
    for value in candidates:
        digest = hashlib.sha256(value).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(value)
        if len(unique) >= limit:
            break
    return unique
