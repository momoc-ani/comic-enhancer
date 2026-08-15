from __future__ import annotations

import httpx

from ...domain import WorkIdentity, WorkMetadata
from ..base import MetadataProvider, confidence, cover, now, text


class JikanMALProvider(MetadataProvider):
    """通过 Jikan 查询并转换 MyAnimeList 漫画元数据。"""

    name = "mal"
    api_url = "https://api.jikan.moe/v4"

    # 方法说明：优先按 MAL 外部 ID 查询，否则搜索并选择最佳作品。
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        url = (
            f"{self.api_url}/manga/{work.external_ids[self.name]}"
            if self.name in work.external_ids
            else f"{self.api_url}/manga"
        )
        params = {} if self.name in work.external_ids else {"q": work.title, "limit": 5}
        with httpx.Client(
            timeout=self.timeout_seconds,
            headers={"User-Agent": "ComicEnhancer/0.1"},
        ) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data")
        item = (
            data
            if isinstance(data, dict)
            else max(
                data or [],
                key=lambda value: confidence(text(value.get("title")), work),
                default=None,
            )
        )
        if not item:
            return None
        title = text(item.get("title"))
        authors = item.get("authors") or []
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("mal_id", "")),
            title=title or work.title,
            title_aliases=[
                text(item.get(key))
                for key in ("title_english", "title_japanese")
                if text(item.get(key))
            ],
            author=text((authors[0] if authors else {}).get("name")),
            summary=text(item.get("synopsis")),
            cover_url=cover(item.get("images", {}).get("jpg")),
            source_url=text(item.get("url")),
            confidence=confidence(title, work),
            fetched_at=now(),
        )
