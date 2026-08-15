from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from ..models import MetadataResolution, WorkIdentity, WorkMetadata
from .base import MetadataProvider
from .providers import (
    AniListProvider,
    BangumiProvider,
    JikanMALProvider,
    KitsuProvider,
    MangaUpdatesProvider,
    ShikimoriProvider,
)


logger = logging.getLogger(__name__)


class MetadataAggregator:
    """并行查询多个提供方并维护作品元数据缓存。"""

    # 方法说明：初始化缓存目录、有效期和提供方列表。
    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        ttl_seconds: int = 86400,
        providers: list[MetadataProvider] | None = None,
    ):
        self.root = root
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.providers = providers if providers is not None else [
            BangumiProvider(),
            AniListProvider(),
            KitsuProvider(),
            ShikimoriProvider(),
            JikanMALProvider(),
            MangaUpdatesProvider(),
        ]
        self.root.mkdir(parents=True, exist_ok=True)

    # 方法说明：聚合并缓存当前作品的外部元数据。
    def resolve(self, work: WorkIdentity) -> MetadataResolution:
        cache_path = self._cache_path(work)
        if not self.enabled:
            return MetadataResolution(work_key=work.key, title=work.title)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return MetadataResolution.model_validate(cached)
        candidates: list[WorkMetadata] = []
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(
            max_workers=min(6, len(self.providers) or 1)
        ) as executor:
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
            (
                item
                for item in candidates
                if item.cover_url and item.confidence >= 0.6
            ),
            max(
                candidates,
                key=lambda item: (
                    item.confidence,
                    self._priority(item.provider),
                ),
                default=None,
            ),
        )
        result = MetadataResolution(
            work_key=work.key,
            title=work.title,
            selected=selected,
            candidates=candidates,
            errors=errors,
        )
        self._write_cache(cache_path, result.model_dump(mode="json"))
        return result

    # 方法说明：读取作品仍在有效期内的元数据缓存且不触发网络请求。
    def cached(self, work: WorkIdentity) -> MetadataResolution | None:
        cached = self._read_cache(self._cache_path(work))
        return MetadataResolution.model_validate(cached) if cached is not None else None

    # 方法说明：调用单个元数据提供方并隔离失败。
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

    # 方法说明：返回元数据提供方的稳定优先级。
    def _priority(self, provider: str) -> int:
        return -next(
            (
                index
                for index, item in enumerate(self.providers)
                if item.name == provider
            ),
            999,
        )

    # 方法说明：生成作品元数据缓存路径。
    def _cache_path(self, work: WorkIdentity) -> Path:
        payload = json.dumps(
            work.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.json"

    # 方法说明：读取并验证元数据缓存文件。
    def _read_cache(self, path: Path) -> dict[str, Any] | None:
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime if path.is_file() else None
        if age is None or age > self.ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # 方法说明：原子写入作品元数据缓存。
    @staticmethod
    def _write_cache(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
