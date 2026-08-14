from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .models import CharacterReference, WorkIdentity


class CharacterIdentityEntry(BaseModel):
    identity_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class WorkIdentityEntry(BaseModel):
    identity_id: str
    title_aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    characters: list[CharacterIdentityEntry] = Field(default_factory=list)


class WorkIdentityRegistry:
    # 方法说明：初始化当前对象及其运行状态。
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
        normalized_title = normalize_title(work.title)
        if not normalized_title:
            return None
        matches = [
            entry
            for entry in self.entries
            if any(
                alias_title_matches(normalized_title, normalize_title(alias))
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


# 方法说明：规范化作品标题以便稳定匹配。
def normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff]+", "", value.casefold())


# 方法说明：规范化角色名称以便别名匹配。
def normalize_character_name(value: str) -> str:
    return normalize_title(value)


# 方法说明：判断作品标题是否完整匹配登记别名。
def alias_title_matches(title: str, alias: str) -> bool:
    if len(alias) < 8:
        return title == alias
    return title == alias or title.startswith(alias) or title.endswith(alias)
