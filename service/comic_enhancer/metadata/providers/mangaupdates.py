from __future__ import annotations

import httpx

from ...domain import WorkIdentity, WorkMetadata
from ...networking import external_http_client
from ..base import MetadataProvider, confidence, now, text


class MangaUpdatesProvider(MetadataProvider):
    """通过可配置的 MangaUpdates API 查询漫画元数据。"""

    name = "mangaupdates"
    default_api_url = "https://api.mangaupdates.com/v1/series/search"

    # 方法说明：初始化可选 API 地址、Token 和请求超时。
    def __init__(
        self,
        *,
        api_url: str = default_api_url,
        api_token: str = "",
        timeout_seconds: int = 8,
    ):
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_url = (api_url or self.default_api_url).rstrip("/")
        self.api_token = api_token

    # 方法说明：搜索并转换 MangaUpdates 漫画元数据。
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        if not self.api_url:
            return None
        headers = {"User-Agent": "ComicEnhancer/0.1"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        with external_http_client(
            self.api_url,
            timeout=self.timeout_seconds,
            headers=headers,
        ) as client:
            response = client.post(
                self.api_url,
                json={"search": work.title, "page": 1, "perpage": 5},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        items = [
            item.get("record", {})
            for item in results
            if isinstance(item, dict)
        ]
        item = max(
            items,
            key=lambda value: confidence(text(value.get("title")), work),
            default=None,
        )
        if not isinstance(item, dict):
            return None
        title = text(item.get("title")) or text(item.get("name"))
        image = item.get("image", {}).get("url", {})
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("series_id") or item.get("id", "")),
            title=title or work.title,
            author=text(item.get("author")),
            summary=text(item.get("description") or item.get("summary")),
            cover_url=text(image.get("original") or image.get("thumb")) or None,
            source_url=text(item.get("url")) or None,
            confidence=confidence(title, work),
            fetched_at=now(),
        )
