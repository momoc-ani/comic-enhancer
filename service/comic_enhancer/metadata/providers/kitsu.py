from __future__ import annotations

from typing import Any

import httpx

from ...domain import WorkIdentity, WorkMetadata
from ...networking import external_http_client
from ..base import MetadataProvider, confidence, cover, now, text


class KitsuProvider(MetadataProvider):
    """查询并转换 Kitsu 漫画元数据。"""

    name = "kitsu"
    api_url = "https://kitsu.io/api/edge"

    # 方法说明：优先按外部 ID 查询，否则搜索并选择最佳作品。
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        params = {"filter[text]": work.title, "page[limit]": "5"}
        if value := work.external_ids.get(self.name):
            url = f"{self.api_url}/manga/{value}"
        else:
            url = f"{self.api_url}/manga"
        with external_http_client(
            url,
            timeout=self.timeout_seconds,
            headers={"User-Agent": "ComicEnhancer/0.1"},
        ) as client:
            response = client.get(url, params={} if "/manga/" in url else params)
            response.raise_for_status()
            payload = response.json()
        item = (
            payload.get("data")
            if "/manga/" in url
            else self._select(payload.get("data", []), work)
        )
        if not item:
            return None
        attributes = item.get("attributes", {})
        titles = attributes.get("titles", {})
        title = (
            text(titles.get("en"))
            or text(titles.get("en_jp"))
            or text(attributes.get("canonicalTitle"))
        )
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=title or work.title,
            title_aliases=[
                text(value)
                for value in titles.values()
                if text(value) and text(value) != title
            ],
            summary=text(attributes.get("synopsis")),
            cover_url=cover(attributes.get("posterImage")),
            source_url=(
                f"https://kitsu.io/manga/{attributes.get('slug') or item.get('id')}"
            ),
            confidence=confidence(title, work),
            fetched_at=now(),
        )

    # 方法说明：从候选项中选择当前请求对应的最佳结果。
    @staticmethod
    def _select(
        items: list[dict[str, Any]],
        work: WorkIdentity,
    ) -> dict[str, Any] | None:
        return max(
            items,
            key=lambda item: confidence(
                text(item.get("attributes", {}).get("canonicalTitle")),
                work,
            ),
            default=None,
        )
