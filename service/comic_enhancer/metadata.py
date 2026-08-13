from __future__ import annotations

import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .models import CharacterReference, MetadataResolution, WorkIdentity, WorkMetadata


logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return ""


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("v", "name", "title", "label"):
            text = _text(value.get(key))
            if text:
                return text
        return ""
    return _text(value)


def _cover(images: Any) -> str | None:
    if isinstance(images, dict):
        for key in ("large", "extraLarge", "original", "medium", "common", "small"):
            value = _text(images.get(key))
            if value:
                return value
    return None


def _confidence(title: str, work: WorkIdentity, *, author: str = "") -> float:
    normalized_title = _text(title).casefold()
    normalized_work_title = _text(work.title).casefold()
    if not normalized_title or not normalized_work_title:
        return 0.0
    score = 0.45
    if normalized_work_title == normalized_title:
        score += 0.35
    elif normalized_title in normalized_work_title or normalized_work_title in normalized_title:
        score += 0.2
    if author and work.author and author.casefold() in work.author.casefold():
        score += 0.15
    return min(score, 1.0)


def _title_confidence(
    titles: list[str],
    work: WorkIdentity,
    *,
    author: str = "",
) -> float:
    return max(
        (_confidence(title, work, author=author) for title in titles if title),
        default=0.0,
    )


class MetadataProvider(ABC):
    name: str

    def __init__(self, *, timeout_seconds: int = 8):
        self.timeout_seconds = timeout_seconds

    @abstractmethod
    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        raise NotImplementedError


class BangumiProvider(MetadataProvider):
    name = "bangumi"
    api_url = "https://api.bgm.tv/v0"

    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        subject_id = work.external_ids.get(self.name)
        headers = {"User-Agent": "ComicEnhancer/0.1 (metadata aggregation)"}
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
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
                                name=_text(item.get("name")),
                                summary=_text(item.get("summary")),
                                image_url=_cover(item.get("images")),
                                relation=_text(item.get("relation")),
                            )
                        )
            title = _text(subject.get("name_cn")) or _text(subject.get("name"))
            aliases = [value for value in (_text(subject.get("name")),) if value and value != title]
            return WorkMetadata(
                provider=self.name,
                provider_id=str(subject_id),
                title=title or work.title,
                title_aliases=aliases,
                author=author,
                summary=_text(subject.get("summary")),
                cover_url=_cover(subject.get("images")),
                source_url=f"https://bgm.tv/subject/{subject_id}" if subject_id else None,
                characters=[item for item in characters if item.name],
                confidence=(
                    1.0
                    if work.external_ids.get(self.name)
                    else _title_confidence([title, *aliases], work, author=author)
                ),
                fetched_at=_now(),
            )

    @staticmethod
    def _select(items: list[dict[str, Any]], work: WorkIdentity) -> dict[str, Any] | None:
        candidates = [item for item in items if _text(item.get("platform")) in {"漫画", "书籍", ""}]
        return max(
            candidates or items,
            key=lambda item: (
                _title_confidence(
                    [_text(item.get("name")), _text(item.get("name_cn"))],
                    work,
                ),
                bool(item.get("series")),
            ),
            default=None,
        )

    @staticmethod
    def _author(infobox: Any) -> str:
        if not isinstance(infobox, list):
            return ""
        for item in infobox:
            if isinstance(item, dict) and _text(item.get("key")) in {"作者", "原作"}:
                return _first_text(item.get("value"))
        return ""


class AniListProvider(MetadataProvider):
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

    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        variables: dict[str, Any] = {}
        if value := work.external_ids.get(self.name):
            variables["id"] = int(value)
        else:
            variables["search"] = work.title
        with httpx.Client(timeout=self.timeout_seconds, headers={"User-Agent": "ComicEnhancer/0.1"}) as client:
            response = client.post(self.api_url, json={"query": self.query, "variables": variables})
            response.raise_for_status()
            media = response.json().get("data", {}).get("Page", {}).get("media", [])
        item = self._select(media, work)
        if not item:
            return None
        title = _text(item.get("title", {}).get("userPreferred")) or _text(item.get("title", {}).get("romaji"))
        aliases = [_text(item.get("title", {}).get(key)) for key in ("english", "native")]
        aliases.extend(_text(value) for value in item.get("synonyms", []))
        authors = [
            _text(edge.get("node", {}).get("name", {}).get("full"))
            for edge in item.get("staff", {}).get("edges", [])
        ]
        characters = [
            CharacterReference(
                provider=self.name,
                provider_id=str(edge.get("node", {}).get("id", "")),
                name=_text(edge.get("node", {}).get("name", {}).get("full")),
                summary=_text(edge.get("node", {}).get("description")),
                image_url=_cover(edge.get("node", {}).get("image")),
                relation=_text(edge.get("role")),
            )
            for edge in item.get("characters", {}).get("edges", [])
        ]
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=title or work.title,
            title_aliases=[value for value in aliases if value and value != title],
            author=authors[0] if authors else "",
            summary=_text(item.get("description")),
            cover_url=_cover(item.get("coverImage")),
            source_url=f"https://anilist.co/manga/{item.get('id')}" if item.get("id") else None,
            characters=[item for item in characters if item.name],
            confidence=(
                1.0
                if work.external_ids.get(self.name)
                else _title_confidence(
                    [title, *aliases],
                    work,
                    author=authors[0] if authors else "",
                )
            ),
            fetched_at=_now(),
        )

    @staticmethod
    def _select(items: list[dict[str, Any]], work: WorkIdentity) -> dict[str, Any] | None:
        if value := work.external_ids.get("anilist"):
            return next((item for item in items if str(item.get("id")) == value), None)
        return max(items, key=lambda item: _confidence(_text(item.get("title", {}).get("userPreferred")), work), default=None)


class KitsuProvider(MetadataProvider):
    name = "kitsu"
    api_url = "https://kitsu.io/api/edge"

    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        params = {"filter[text]": work.title, "page[limit]": "5"}
        if value := work.external_ids.get(self.name):
            url = f"{self.api_url}/manga/{value}"
        else:
            url = f"{self.api_url}/manga"
        with httpx.Client(timeout=self.timeout_seconds, headers={"User-Agent": "ComicEnhancer/0.1"}) as client:
            response = client.get(url, params={} if "/manga/" in url else params)
            response.raise_for_status()
            payload = response.json()
        item = payload.get("data") if "/manga/" in url else self._select(payload.get("data", []), work)
        if not item:
            return None
        attributes = item.get("attributes", {})
        titles = attributes.get("titles", {})
        title = _text(titles.get("en")) or _text(titles.get("en_jp")) or _text(attributes.get("canonicalTitle"))
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=title or work.title,
            title_aliases=[_text(value) for value in titles.values() if _text(value) and _text(value) != title],
            summary=_text(attributes.get("synopsis")),
            cover_url=_cover(attributes.get("posterImage")),
            source_url=f"https://kitsu.io/manga/{attributes.get('slug') or item.get('id')}",
            confidence=_confidence(title, work),
            fetched_at=_now(),
        )

    @staticmethod
    def _select(items: list[dict[str, Any]], work: WorkIdentity) -> dict[str, Any] | None:
        return max(items, key=lambda item: _confidence(_text(item.get("attributes", {}).get("canonicalTitle")), work), default=None)


class ShikimoriProvider(MetadataProvider):
    name = "shikimori"
    api_url = "https://shikimori.one/api"

    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        params = {"search": work.title, "limit": 5}
        if value := work.external_ids.get(self.name):
            url = f"{self.api_url}/mangas/{value}"
            params = {}
        else:
            url = f"{self.api_url}/mangas"
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers={"User-Agent": "ComicEnhancer/0.1"}) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        item = payload if isinstance(payload, dict) else max(payload, key=lambda value: _confidence(_text(value.get("name")), work), default=None)
        if not item:
            return None
        title = _text(item.get("russian")) or _text(item.get("name"))
        image = item.get("image", {})
        image_url = _text(image.get("original"))
        if image_url and image_url.startswith("/"):
            image_url = "https://shikimori.io" + image_url
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=title or work.title,
            title_aliases=[_text(item.get("name")), *[_text(value) for value in item.get("synonyms", [])]],
            summary=_text(item.get("description")),
            cover_url=image_url,
            source_url=f"https://shikimori.one/mangas/{item.get('id')}",
            confidence=_confidence(title, work),
            fetched_at=_now(),
        )


class JikanMALProvider(MetadataProvider):
    name = "mal"
    api_url = "https://api.jikan.moe/v4"

    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        url = f"{self.api_url}/manga/{work.external_ids[self.name]}" if self.name in work.external_ids else f"{self.api_url}/manga"
        params = {} if self.name in work.external_ids else {"q": work.title, "limit": 5}
        with httpx.Client(timeout=self.timeout_seconds, headers={"User-Agent": "ComicEnhancer/0.1"}) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        item = payload.get("data") if isinstance(payload.get("data"), dict) else max(payload.get("data", []), key=lambda value: _confidence(_text(value.get("title")), work), default=None)
        if not item:
            return None
        title = _text(item.get("title"))
        authors = item.get("authors") or []
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("mal_id", "")),
            title=title or work.title,
            title_aliases=[_text(item.get(key)) for key in ("title_english", "title_japanese") if _text(item.get(key))],
            author=_text((authors[0] if authors else {}).get("name")),
            summary=_text(item.get("synopsis")),
            cover_url=_cover(item.get("images", {}).get("jpg")),
            source_url=_text(item.get("url")),
            confidence=_confidence(title, work),
            fetched_at=_now(),
        )


class MangaUpdatesProvider(MetadataProvider):
    """MangaUpdates public API adapter; credentials remain optional."""

    name = "mangaupdates"
    default_api_url = "https://api.mangaupdates.com/v1/series/search"

    def __init__(self, *, api_url: str = default_api_url, api_token: str = "", timeout_seconds: int = 8):
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_url = (api_url or self.default_api_url).rstrip("/")
        self.api_token = api_token

    def search(self, work: WorkIdentity) -> WorkMetadata | None:
        if not self.api_url:
            return None
        headers = {"User-Agent": "ComicEnhancer/0.1"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
            response = client.post(
                self.api_url,
                json={"search": work.title, "page": 1, "perpage": 5},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        items = [item.get("record", {}) for item in results if isinstance(item, dict)]
        item = max(items, key=lambda value: _confidence(_text(value.get("title")), work), default=None)
        if not isinstance(item, dict):
            return None
        title = _text(item.get("title")) or _text(item.get("name"))
        image = item.get("image", {}).get("url", {})
        return WorkMetadata(
            provider=self.name,
            provider_id=str(item.get("series_id") or item.get("id", "")),
            title=title or work.title,
            author=_text(item.get("author")),
            summary=_text(item.get("description") or item.get("summary")),
            cover_url=_text(image.get("original") or image.get("thumb")) or None,
            source_url=_text(item.get("url")) or None,
            confidence=_confidence(title, work),
            fetched_at=_now(),
        )


class MetadataAggregator:
    def __init__(self, root: Path, *, enabled: bool = True, ttl_seconds: int = 86400, providers: list[MetadataProvider] | None = None):
        self.root = root
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.providers = providers if providers is not None else [BangumiProvider(), AniListProvider(), KitsuProvider(), ShikimoriProvider(), JikanMALProvider(), MangaUpdatesProvider()]
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, work: WorkIdentity) -> MetadataResolution:
        cache_path = self._cache_path(work)
        if not self.enabled:
            return MetadataResolution(work_key=work.key, title=work.title)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return MetadataResolution.model_validate(cached)
        candidates: list[WorkMetadata] = []
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(6, len(self.providers) or 1)) as executor:
            responses = executor.map(
                lambda provider: self._query_provider(provider, work),
                self.providers,
            )
            for provider_name, metadata, error in responses:
                if metadata:
                    candidates.append(metadata)
                if error:
                    errors[provider_name] = error
        selected = next(
            (item for item in candidates if item.cover_url and item.confidence >= 0.6),
            max(candidates, key=lambda item: (item.confidence, self._priority(item.provider)), default=None),
        )
        result = MetadataResolution(work_key=work.key, title=work.title, selected=selected, candidates=candidates, errors=errors)
        self._write_cache(cache_path, result.model_dump(mode="json"))
        return result

    def cached(self, work: WorkIdentity) -> MetadataResolution | None:
        """读取未过期缓存，不触发网络请求。"""
        cached = self._read_cache(self._cache_path(work))
        return MetadataResolution.model_validate(cached) if cached is not None else None

    @staticmethod
    def _query_provider(
        provider: MetadataProvider,
        work: WorkIdentity,
    ) -> tuple[str, WorkMetadata | None, str]:
        try:
            return provider.name, provider.search(work), ""
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
            logger.info("元数据提供方不可用 %s: %s", provider.name, error)
            return provider.name, None, str(error)

    def _priority(self, provider: str) -> int:
        return -next((index for index, item in enumerate(self.providers) if item.name == provider), 999)

    def _cache_path(self, work: WorkIdentity) -> Path:
        payload = json.dumps(work.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.json"

    def _read_cache(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file() or (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) > self.ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_cache(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
