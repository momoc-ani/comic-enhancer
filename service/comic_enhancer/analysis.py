from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx

from .models import CharacterBankEntry, ChapterAnalysisResult, PageAnalysis


class PageAnalysisStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def image_hash(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    def get(self, image_bytes: bytes, *, work_key: str = "") -> PageAnalysis | None:
        image_hash = self.image_hash(image_bytes)
        path = self._path(image_hash, work_key)
        if not path.is_file():
            return None
        try:
            return PageAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, analysis: PageAnalysis, *, work_key: str = "") -> None:
        path = self._path(analysis.image_hash, work_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".json.{os.getpid()}.tmp")
        temporary.write_text(analysis.model_dump_json(), encoding="utf-8")
        temporary.replace(path)

    def _path(self, image_hash: str, work_key: str = "") -> Path:
        namespace = (
            hashlib.sha256(work_key.encode("utf-8")).hexdigest()[:16]
            if work_key
            else "unscoped"
        )
        return self.root / namespace / image_hash[:2] / f"{image_hash}.json"


class ChapterAnalyzerClient:
    def __init__(self, base_url: str, *, timeout_seconds: int = 180):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/v1/health", timeout=2)
            return response.status_code == 200 and bool(response.json().get("ready"))
        except (httpx.HTTPError, ValueError):
            return False

    def analyze(
        self,
        pages: list[tuple[str, bytes]],
        character_bank: list[tuple[CharacterBankEntry, bytes]],
    ) -> ChapterAnalysisResult:
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for filename, image_bytes in pages:
            files.append(("pages", (filename, image_bytes, "application/octet-stream")))
        for index, (_, image_bytes) in enumerate(character_bank):
            files.append(
                (
                    "character_images",
                    (f"character-{index}.png", image_bytes, "application/octet-stream"),
                )
            )
        bank_json = json.dumps(
            [entry.model_dump(mode="json") for entry, _ in character_bank],
            ensure_ascii=False,
        )
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            response = client.post(
                "/v1/analyze/chapter",
                data={"character_json": bank_json},
                files=files,
            )
            response.raise_for_status()
            return ChapterAnalysisResult.model_validate(response.json())
