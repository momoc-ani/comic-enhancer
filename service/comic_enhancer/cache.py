from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import ProcessOptions, ResolvedAdapter, WorkIdentity


class ResultCache:
    # 方法说明：初始化当前对象及其运行状态。
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # 方法说明：生成当前模型或缓存对象的稳定键。
    def key(
        self,
        image_bytes: bytes,
        reference_bytes: bytes | None,
        work: WorkIdentity,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        backend_revision: str = "",
    ) -> str:
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        reference_hash = (
            hashlib.sha256(reference_bytes).hexdigest()
            if reference_bytes is not None
            else "none"
        )
        adapter_revision = (
            f"{resolved.adapter.adapter_id}:{resolved.adapter.revision}"
            if resolved.adapter
            else "none"
        )
        payload = "|".join(
            [
                image_hash,
                reference_hash,
                work.key,
                options.mode,
                options.palette_version,
                adapter_revision,
                backend_revision,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # 方法说明：返回缓存结果图的存储路径。
    def result_path(self, cache_key: str, suffix: str = ".webp") -> Path:
        return self.root / cache_key[:2] / f"{cache_key}{suffix}"

    # 方法说明：返回缓存元数据的存储路径。
    def metadata_path(self, cache_key: str) -> Path:
        return self.root / cache_key[:2] / f"{cache_key}.json"

    # 方法说明：读取指定缓存键的元数据。
    def load_metadata(self, cache_key: str) -> dict[str, object]:
        path = self.metadata_path(cache_key)
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    # 方法说明：原子保存指定缓存键的元数据。
    def save_metadata(self, cache_key: str, metadata: dict[str, object]) -> None:
        path = self.metadata_path(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".json.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
