from __future__ import annotations

from typing import Any

import httpx

from ...domain import CharacterReference, WorkIdentity, WorkMetadata
from ...networking import external_http_client
from ..base import MetadataProvider, confidence, cover, now, text, title_confidence


class AniListProvider(MetadataProvider):
    """查询并转换 AniList 作品与角色元数据。"""

    name = "anilist"
    api_url = "https://graphql.anilist.co"
    query = """
    query ($search: String, $id: Int) {
      Page(perPage: 5) {
        media(search: $search, id: $id, type: MANGA) {
          id title { romaji english native userPreferred } synonyms description
          coverImage { extraLarge large medium }
          staff(perPage: 4) { edges { node { name { full } } } }
          characters(perPage: 16, sort: [ROLE, RELEVANCE]) { edges { node { id name { full native } description image { large medium } } role } }
        }
      }
    }
    """

    # 方法说明：优先按外部 ID 查询，否则搜索并选择最佳作品。
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        variables: dict[str, Any] = {}
        if value := work.external_ids.get(self.name):
            variables["id"] = int(value)
        else:
            variables["search"] = work.title
        with external_http_client(
            self.api_url,
            timeout=self.timeout_seconds,
            headers={"User-Agent": "ComicEnhancer/0.1"},
        ) as client:
            response = client.post(
                self.api_url,
                json={"query": self.query, "variables": variables},
            )
            response.raise_for_status()
            media = response.json().get("data", {}).get("Page", {}).get("media", [])
        item = self._select(media, work)
        if not item:
            return None
        title = text(item.get("title", {}).get("userPreferred")) or text(
            item.get("title", {}).get("romaji")
        )
        aliases = [text(item.get("title", {}).get(key)) for key in ("english", "native")]
        aliases.extend(text(value) for value in item.get("synonyms", []))
        authors = [
            text(edge.get("node", {}).get("name", {}).get("full"))
            for edge in item.get("staff", {}).get("edges", [])
        ]
        characters = [
            CharacterReference(
                provider=self.name,
                provider_id=str(edge.get("node", {}).get("id", "")),
                name=text(edge.get("node", {}).get("name", {}).get("full")),
                summary=text(edge.get("node", {}).get("description")),
                image_url=cover(edge.get("node", {}).get("image")),
                relation=text(edge.get("role")),
            )
            for edge in item.get("characters", {}).get("edges", [])
        ]
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=title or work.title,
            title_aliases=[value for value in aliases if value and value != title],
            author=authors[0] if authors else "",
            summary=text(item.get("description")),
            cover_url=cover(item.get("coverImage")),
            source_url=(
                f"https://anilist.co/manga/{item.get('id')}" if item.get("id") else None
            ),
            characters=[item for item in characters if item.name],
            confidence=(
                1.0
                if work.external_ids.get(self.name)
                else title_confidence(
                    [title, *aliases],
                    work,
                    author=authors[0] if authors else "",
                )
            ),
            fetched_at=now(),
        )

    # 方法说明：从候选项中选择当前请求对应的最佳结果。
    @staticmethod
    def _select(
        items: list[dict[str, Any]],
        work: WorkIdentity,
    ) -> dict[str, Any] | None:
        if value := work.external_ids.get("anilist"):
            return next((item for item in items if str(item.get("id")) == value), None)
        return max(
            items,
            key=lambda item: confidence(
                text(item.get("title", {}).get("userPreferred")),
                work,
            ),
            default=None,
        )
