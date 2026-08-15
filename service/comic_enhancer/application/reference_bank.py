from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from ..domain import (
    CharacterBankEntry,
    MetadataResolution,
    WorkIdentity,
    WorkMetadata,
)
from ..identities import WorkIdentityRegistry
from ..references import (
    ReferenceImageQuality,
    ReferenceImageStore,
    assess_reference_image,
    reference_quality_rank,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ReferenceCandidate:
    name: str
    image_url: str
    provider: str
    image_bytes: bytes
    quality: ReferenceImageQuality
    confirmed_source: bool


def prioritized_metadata_candidates(
    resolution: MetadataResolution,
    work: WorkIdentity,
) -> list[WorkMetadata]:
    """优先返回外部 ID 精确匹配的元数据候选。"""
    indexed = list(enumerate(resolution.candidates))
    indexed.sort(
        key=lambda item: (
            -int(
                work.external_ids.get(item[1].provider)
                == item[1].provider_id
            ),
            -item[1].confidence,
            item[0],
        )
    )
    return [candidate for _, candidate in indexed]


@dataclass
class ReferenceBankService:
    references: ReferenceImageStore
    identities: WorkIdentityRegistry

    async def build(
        self,
        resolution: MetadataResolution,
        work: WorkIdentity,
    ) -> list[tuple[CharacterBankEntry, bytes]]:
        """从缓存元数据构建角色参考图库。"""
        grouped: dict[str, list[_ReferenceCandidate]] = {}
        seen: set[str] = set()
        for candidate in prioritized_metadata_candidates(resolution, work):
            if candidate.confidence < 0.6:
                continue
            for character in candidate.characters:
                key = f"{character.provider}:{character.provider_id}"
                if key in seen or not character.image_url:
                    continue
                image_bytes = await asyncio.to_thread(
                    self.references.get,
                    character.image_url,
                )
                if image_bytes is None:
                    continue
                character_id, character_name = self.identities.canonical_character(
                    work,
                    character,
                )
                grouped.setdefault(character_id, []).append(
                    _ReferenceCandidate(
                        name=character_name,
                        image_url=character.image_url,
                        provider=character.provider,
                        image_bytes=image_bytes,
                        quality=assess_reference_image(image_bytes),
                        confirmed_source=(
                            work.external_ids.get(candidate.provider)
                            == candidate.provider_id
                        ),
                    )
                )
                seen.add(key)

        entry_groups: list[list[tuple[CharacterBankEntry, bytes]]] = []
        decisions: list[dict[str, object]] = []
        for character_id, candidates in grouped.items():
            selected = self._select_character(character_id, candidates, work)
            if selected is None:
                continue
            entries, decision = selected
            entry_groups.append(entries)
            decisions.append(decision)

        logger.info(
            "角色参考图择优完成 work=%s selections=%s",
            work.key,
            json.dumps(decisions[:16], ensure_ascii=False),
        )
        entries = [
            group[view_index]
            for view_index in range(max((len(group) for group in entry_groups), default=0))
            for group in entry_groups
            if view_index < len(group)
        ]
        if len(entries) > 16:
            logger.warning("角色匹配视图超过 16 个，仅保留前 16 个: work=%s", work.key)
            return entries[:16]
        return entries

    def _select_character(
        self,
        character_id: str,
        candidates: list[_ReferenceCandidate],
        work: WorkIdentity,
    ) -> tuple[list[tuple[CharacterBankEntry, bytes]], dict[str, object]] | None:
        """为单个角色选择最佳参考图和可匹配视图。"""
        eligible = [
            item for item in candidates if item.quality.usable and item.quality.colorful
        ]
        if not eligible:
            logger.info(
                "角色参考图均不满足彩色质量门槛，跳过: work=%s character=%s",
                work.key,
                character_id,
            )
            return None
        best = max(eligible, key=self._rank)
        portrait = max(
            (item for item in eligible if not item.quality.full_body),
            key=self._rank,
            default=None,
        )
        full_body = max(
            (item for item in eligible if item.quality.full_body),
            key=self._rank,
            default=None,
        )
        match_views = [item for item in candidates if item.quality.usable]
        match_views.sort(key=self._rank, reverse=True)
        entries = [
            (
                CharacterBankEntry(
                    character_id=character_id,
                    name=best.name,
                    image_url=best.image_url,
                    provider=view.provider,
                    portrait_reference_url=portrait.image_url if portrait else None,
                    full_body_reference_url=full_body.image_url if full_body else None,
                ),
                view.image_bytes,
            )
            for view in match_views
        ]
        decision = {
            "character_id": character_id,
            "name": best.name,
            "provider": best.provider,
            "portrait_provider": portrait.provider if portrait else None,
            "full_body_provider": full_body.provider if full_body else None,
            "size": f"{best.quality.width}x{best.quality.height}",
            "saturation": round(best.quality.saturation, 1),
            "alternatives": len(candidates),
            "match_views": len(match_views),
        }
        return entries, decision

    @staticmethod
    def _rank(candidate: _ReferenceCandidate) -> tuple[object, ...]:
        """计算角色参考图的稳定质量排序键。"""
        return reference_quality_rank(
            candidate.quality,
            confirmed_source=candidate.confirmed_source,
            provider=candidate.provider,
        )
