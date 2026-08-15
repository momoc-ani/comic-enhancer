from __future__ import annotations

from typing import Any

import httpx

from ...models import CharacterReference, WorkIdentity, WorkMetadata
from ..base import (
    MetadataProvider,
    cover,
    first_text,
    now,
    text,
    title_confidence,
)


class BangumiProvider(MetadataProvider):
    """查询并转换 Bangumi 作品与角色元数据。"""

    name = "bangumi"
    api_url = "https://api.bgm.tv/v0"

    # 方法说明：优先按外部 ID 查询，否则搜索并选择最佳作品。
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        subject_id = work.external_ids.get(self.name)
        headers = {"User-Agent": "ComicEnhancer/0.1 (metadata aggregation)"}
        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            if subject_id:
                response = client.get(f"{self.api_url}/subjects/{subject_id}")
                response.raise_for_status()
                subject = response.json()
            else:
                response = client.post(
                    f"{self.api_url}/search/subjects",
                    json={"keyword": work.title, "filter": {"type": [1]}, "limit": 5},
                )
                response.raise_for_status()
                subject = self._select(response.json().get("data", []), work)
                if not subject:
                    return None
                subject_id = str(subject.get("id", ""))
                detail = client.get(f"{self.api_url}/subjects/{subject_id}")
                if detail.is_success:
                    subject = detail.json()

            author = self._author(subject.get("infobox"))
            characters: list[CharacterReference] = []
            if subject_id:
                response = client.get(f"{self.api_url}/subjects/{subject_id}/characters")
                if response.is_success:
                    for item in response.json()[:24]:
                        characters.append(
                            CharacterReference(
                                provider=self.name,
                                provider_id=str(item.get("id", "")),
                                name=text(item.get("name")),
                                summary=text(item.get("summary")),
                                image_url=cover(item.get("images")),
                                relation=text(item.get("relation")),
                            )
                        )
        title = text(subject.get("name_cn")) or text(subject.get("name"))
        aliases = [
            value
            for value in (text(subject.get("name")),)
            if value and value != title
        ]
        return WorkMetadata(
            provider=self.name,
            provider_id=str(subject_id),
            title=title or work.title,
            title_aliases=aliases,
            author=author,
            summary=text(subject.get("summary")),
            cover_url=cover(subject.get("images")),
            source_url=f"https://bgm.tv/subject/{subject_id}" if subject_id else None,
            characters=[item for item in characters if item.name],
            confidence=(
                1.0
                if work.external_ids.get(self.name)
                else title_confidence([title, *aliases], work, author=author)
            ),
            fetched_at=now(),
        )

    # 方法说明：从候选项中选择当前请求对应的最佳结果。
    @staticmethod
    def _select(
        items: list[dict[str, Any]],
        work: WorkIdentity,
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in items
            if text(item.get("platform")) in {"漫画", "书籍", ""}
        ]
        return max(
            candidates or items,
            key=lambda item: (
                title_confidence(
                    [text(item.get("name")), text(item.get("name_cn"))],
                    work,
                ),
                bool(item.get("series")),
            ),
            default=None,
        )

    # 方法说明：从 Bangumi 信息栏中提取作者名称。
    @staticmethod
    def _author(infobox: Any) -> str:
        if not isinstance(infobox, list):
            return ""
        for item in infobox:
            if isinstance(item, dict) and text(item.get("key")) in {"作者", "原作"}:
                return first_text(item.get("value"))
        return ""
