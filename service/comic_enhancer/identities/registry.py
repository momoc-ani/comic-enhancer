from __future__ import annotations

import json
from pathlib import Path

from ..domain import CharacterReference, WorkIdentity
from .matching import (
    alias_title_matches,
    normalize_character_name,
    normalize_title,
)
from .models import WorkIdentityEntry


class WorkIdentityRegistry:
    """读取已确认作品映射并合并跨提供方身份。"""

    # 方法说明：读取并校验本地作品身份配置。
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

    # 方法说明：使用本地身份登记补全作品信息。
    def enrich(self, work: WorkIdentity) -> WorkIdentity:
        entry = self.match(work)
        if entry is None:
            return work
        external_ids = {**entry.external_ids, **work.external_ids}
        return work.model_copy(update={"external_ids": external_ids})

    # 方法说明：查找与作品身份唯一匹配的登记项。
    def match(self, work: WorkIdentity) -> WorkIdentityEntry | None:
        normalized_titles = {
            normalized
            for title in [work.title, *work.title_aliases]
            if (normalized := normalize_title(title))
        }
        if not normalized_titles:
            return None
        matches = [
            entry
            for entry in self.entries
            if any(
                alias_title_matches(title, normalize_title(alias))
                for title in normalized_titles
                for alias in entry.title_aliases
            )
        ]
        return matches[0] if len(matches) == 1 else None

    # 方法说明：合并并返回角色的规范身份信息。
    def canonical_character(
        self,
        work: WorkIdentity,
        character: CharacterReference,
    ) -> tuple[str, str]:
        entry = self.match(work)
        if entry is not None:
            normalized_name = normalize_character_name(character.name)
            for configured in entry.characters:
                provider_id = configured.external_ids.get(character.provider)
                names = [configured.name, *configured.aliases]
                if (
                    provider_id == character.provider_id
                    or normalized_name
                    and normalized_name
                    in {normalize_character_name(name) for name in names}
                ):
                    return (
                        f"work:{entry.identity_id}:{configured.identity_id}",
                        configured.name,
                    )
        return f"{character.provider}:{character.provider_id}", character.name
