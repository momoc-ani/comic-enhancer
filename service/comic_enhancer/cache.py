from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import ProcessOptions, ResolvedAdapter, WorkIdentity


class ResultCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def key(
        self,
        image_bytes: bytes,
        work: WorkIdentity,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> str:
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        adapter_revision = (
            f"{resolved.adapter.adapter_id}:{resolved.adapter.revision}"
            if resolved.adapter
            else "none"
        )
        payload = "|".join(
            [
                image_hash,
                work.key,
                options.mode,
                options.palette_version,
                adapter_revision,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def result_path(self, cache_key: str, suffix: str = ".webp") -> Path:
        return self.root / cache_key[:2] / f"{cache_key}{suffix}"

    def metadata_path(self, cache_key: str) -> Path:
        return self.root / cache_key[:2] / f"{cache_key}.json"

    def load_metadata(self, cache_key: str) -> dict[str, object]:
        path = self.metadata_path(cache_key)
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_metadata(self, cache_key: str, metadata: dict[str, object]) -> None:
        path = self.metadata_path(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".json.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
