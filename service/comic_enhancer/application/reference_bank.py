from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
import time

from ..domain import (
    CharacterBankEntry,
    MetadataResolution,
    WorkIdentity,
    WorkMetadata,
)
from ..identities import WorkIdentityRegistry
from ..logging_utils import log_operation
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
    summary: str
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
        started = time.perf_counter()
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
                        summary=character.summary,
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
        for character_id, candidates in grouped.items():
            selected = self._select_character(character_id, candidates, work)
            if selected is None:
                continue
            entries, _ = selected
            entry_groups.append(entries)

        entries = [
            group[view_index]
            for view_index in range(max((len(group) for group in entry_groups), default=0))
            for group in entry_groups
            if view_index < len(group)
        ]
        truncated = len(entries) > 16
        selected_entries = entries[:16]
        log_operation(
            logger,
            logging.INFO,
            feature="角色参考图库构建",
            parameters={
                "work_key": work.key,
                "metadata_candidates": len(resolution.candidates),
            },
            result={
                "status": "success",
                "candidate_characters": len(grouped),
                "selected_characters": len(entry_groups),
                "reference_views": len(selected_entries),
                "truncated": truncated,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return selected_entries

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
            log_operation(
                logger,
                logging.INFO,
                feature="角色参考图质量筛选",
                parameters={
                    "work_key": work.key,
                    "character_id": character_id,
                    "candidates": len(candidates),
                },
                result={"eligible": False, "usable_color_views": 0},
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
                    summary=best.summary,
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
