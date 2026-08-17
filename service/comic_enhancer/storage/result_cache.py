from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

from ..domain import ProcessOptions, WorkIdentity


class ResultCache:
    """生成稳定缓存键并存储推理结果元数据。"""

    # 方法说明：初始化推理结果缓存根目录。
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._remove_incomplete_temporary_files()

    # 方法说明：清理上次异常退出留下的结果临时文件，不触碰已提交缓存。
    def _remove_incomplete_temporary_files(self) -> None:
        for pattern in (".*.webp", ".*.json.*.tmp"):
            for path in self.root.rglob(pattern):
                if path.is_file():
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue

    # 方法说明：生成覆盖页面、参考图、作品、档位和模型版本的缓存键。
    def key(
        self,
        image_bytes: bytes,
        reference_bytes: bytes | None,
        work: WorkIdentity,
        options: ProcessOptions,
        backend_revision: str = "",
    ) -> str:
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        reference_hash = (
            hashlib.sha256(reference_bytes).hexdigest()
            if reference_bytes is not None
            else "none"
        )
        payload = "|".join(
            [
                image_hash,
                reference_hash,
                work.key,
                options.mode,
                options.palette_version,
                "comfyui-direct-output" if options.comfyui_direct_output else "",
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

    # 方法说明：校验结果文件与提交元数据是否构成完整的可恢复缓存。
    def is_complete(self, cache_key: str) -> bool:
        result_path = self.result_path(cache_key)
        metadata = self.load_metadata(cache_key)
        if not result_path.is_file() or not metadata:
            return False
        try:
            if "cache_key" not in metadata and "result_bytes" not in metadata:
                return result_path.stat().st_size > 0
            return (
                metadata.get("cache_key") == cache_key
                and result_path.stat().st_size == int(metadata.get("result_bytes", -1))
                and result_path.stat().st_size > 0
            )
        except (OSError, TypeError, ValueError):
            return False

    # 方法说明：为一次推理创建与最终扩展名一致的临时输出路径。
    def temporary_result_path(self, cache_key: str) -> Path:
        output_path = self.result_path(cache_key)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path.parent / f".{cache_key}.{os.getpid()}.{uuid.uuid4().hex}.webp"

    # 方法说明：原子提交已完整写入的临时结果并返回最终路径。
    def commit_result(self, cache_key: str, temporary: Path) -> Path:
        output_path = self.result_path(cache_key)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise ValueError("推理后端没有生成有效结果文件")
        temporary.replace(output_path)
        return output_path

    # 方法说明：原子保存指定缓存键的元数据。
    def save_metadata(self, cache_key: str, metadata: dict[str, object]) -> None:
        path = self.metadata_path(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".json.{os.getpid()}.tmp")
        result_path = self.result_path(cache_key)
        committed = {
            **metadata,
            "cache_key": cache_key,
            "result_bytes": result_path.stat().st_size,
        }
        temporary.write_text(
            json.dumps(committed, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
