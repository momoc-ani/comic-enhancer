from __future__ import annotations

import httpx

from ...domain import WorkIdentity, WorkMetadata
from ..base import MetadataProvider, confidence, now, text


class ShikimoriProvider(MetadataProvider):
    """查询并转换 Shikimori 漫画元数据。"""

    name = "shikimori"
    api_url = "https://shikimori.one/api"

    # 方法说明：优先按外部 ID 查询，否则搜索并选择最佳作品。
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        params = {"search": work.title, "limit": 5}
        if value := work.external_ids.get(self.name):
            url = f"{self.api_url}/mangas/{value}"
            params = {}
        else:
            url = f"{self.api_url}/mangas"
        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "ComicEnhancer/0.1"},
        ) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        item = (
            payload
            if isinstance(payload, dict)
            else max(
                payload,
                key=lambda value: confidence(text(value.get("name")), work),
                default=None,
            )
        )
        if not item:
            return None
        title = text(item.get("russian")) or text(item.get("name"))
        image_url = text(item.get("image", {}).get("original"))
        if image_url and image_url.startswith("/"):
            image_url = "https://shikimori.io" + image_url
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=title or work.title,
            title_aliases=[
                text(item.get("name")),
                *[text(value) for value in item.get("synonyms", [])],
            ],
            summary=text(item.get("description")),
            cover_url=image_url,
            source_url=f"https://shikimori.one/mangas/{item.get('id')}",
            confidence=confidence(title, work),
            fetched_at=now(),
        )
