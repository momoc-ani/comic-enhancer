from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .models import WorkIdentity


class WorkIdentityEntry(BaseModel):
    identity_id: str
    title_aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class WorkIdentityRegistry:
    def __init__(self, path: Path | None):
        self.entries: list[WorkIdentityEntry] = []
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.entries = [
                WorkIdentityEntry.model_validate(item)
                for item in payload.get("works", [])
            ]
        except (OSError, ValueError, json.JSONDecodeError):
            self.entries = []

    def enrich(self, work: WorkIdentity) -> WorkIdentity:
        normalized_title = normalize_title(work.title)
        if not normalized_title:
            return work
        matches = [
            entry
            for entry in self.entries
            if any(
                alias_title_matches(normalized_title, normalize_title(alias))
                for alias in entry.title_aliases
            )
        ]
        if len(matches) != 1:
            return work
        external_ids = {**matches[0].external_ids, **work.external_ids}
        return work.model_copy(update={"external_ids": external_ids})


def normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff]+", "", value.casefold())


def alias_title_matches(title: str, alias: str) -> bool:
    if len(alias) < 8:
        return title == alias
    return title == alias or title.startswith(alias) or title.endswith(alias)
