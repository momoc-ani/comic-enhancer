from __future__ import annotations

import hashlib
import json
import logging
import time

from ..character_vision import (
    PAGE_TEMPLATE_REVISION,
    PROFILE_TEMPLATE_REVISION,
    CharacterPageAnalysis,
    CharacterVisionAnalyzer,
    PageCharacterMatch,
)
from ..logging_utils import log_operation
from .color_sampler import sample_profile_colors
from .embeddings import ImageEmbeddingExtractor
from .models import (
    CharacterPageContext,
    CharacterPromptContext,
    CharacterProfile,
    CharacterReferenceAsset,
    PreparedCharacter,
)
from .repository import (
    CharacterLibraryRepository,
    page_plan_cache_key,
    profile_cache_key,
)


logger = logging.getLogger(__name__)


PAGE_INSTANCE_CONFIDENCE_FLOOR = 0.85
DUPLICATE_INSTANCE_OVERLAP_RATIO = 0.45


class CharacterLibraryBuilder:
    """构建并缓存角色档案与页面身份绑定计划。"""

    # 方法说明：初始化角色库、视觉分析器和置信度门槛。
    def __init__(
        self,
        *,
        repository: CharacterLibraryRepository,
        analyzer: CharacterVisionAnalyzer,
        min_confidence: float = 0.75,
    ):
        self.repository = repository
        self.analyzer = analyzer
        self.min_confidence = max(0.5, min(0.99, min_confidence))
        self.embedding_extractor = ImageEmbeddingExtractor()

    @property
    def model_revision(self) -> str:
        """返回当前角色视觉分析模型版本。"""
        return self.analyzer.model_revision

    # 方法说明：检查角色视觉分析 sidecar 是否可用。
    def ready(self) -> bool:
        return self.analyzer.ready()

    # 方法说明：只准备静态角色档案，不分析当前漫画页面。
    def prepare_prompt_context(
        self,
        *,
        work_key: str,
        references: tuple[CharacterReferenceAsset, ...],
    ) -> CharacterPromptContext:
        started = time.perf_counter()
        prepared = self._prepare_profiles(work_key, references)
        digest = self._profile_context_digest(prepared)
        log_operation(
            logger,
            logging.INFO,
            feature="角色静态提示上下文准备",
            parameters={
                "work_key": work_key,
                "references": min(len(references), 3),
            },
            result={
                "status": "success",
                "profiles": len(prepared),
                "colors": sum(len(item.profile.colors) for item in prepared),
                "context_digest": digest[:12],
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return CharacterPromptContext(tuple(prepared), digest)

    # 方法说明：准备最多三个角色档案及当前页高置信度角色计划。
    def prepare(
        self,
        *,
        work_key: str,
        image_bytes: bytes,
        references: tuple[CharacterReferenceAsset, ...],
    ) -> CharacterPageContext:
        started = time.perf_counter()
        page_digest = hashlib.sha256(image_bytes).hexdigest()[:12]
        prepared = self._prepare_profiles(work_key, references)
        page_key = page_plan_cache_key(
            work_key=work_key,
            image_bytes=image_bytes,
            profile_digests=[item.profile.digest for item in prepared],
            model_revision=self.analyzer.model_revision,
            template_revision=PAGE_TEMPLATE_REVISION,
        )
        analysis = self.repository.load_page_plan(page_key)
        page_cache_hit = analysis is not None
        if analysis is None:
            analysis = self.analyzer.analyze_page(
                image_bytes=image_bytes,
                candidates=[self._candidate(item) for item in prepared],
            )
            analysis = self._validate_page_analysis(analysis, prepared)
            self.repository.save_page_plan(page_key, work_key, analysis)
        else:
            analysis = self._validate_page_analysis(analysis, prepared)
        digest = self._context_digest(prepared, analysis)
        log_operation(
            logger,
            logging.INFO,
            feature="角色页面计划准备",
            parameters={
                "work_key": work_key,
                "page_sha256": page_digest,
                "references": min(len(references), 3),
                "confidence_threshold": self.min_confidence,
            },
            result={
                "status": "success",
                "profiles": len(prepared),
                "visible_characters": sum(
                    1 for match in analysis.characters if match.visible
                ),
                "instances": sum(
                    len(match.instances) for match in analysis.characters
                ),
                "page_cache_hit": page_cache_hit,
                "context_digest": digest[:12],
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return CharacterPageContext(tuple(prepared), analysis, digest)

    # 方法说明：读取或构建最多三个角色档案并保持参考图槽位顺序。
    def _prepare_profiles(
        self,
        work_key: str,
        references: tuple[CharacterReferenceAsset, ...],
    ) -> list[PreparedCharacter]:
        if not references:
            raise RuntimeError("角色稳定档需要至少一张角色参考图")
        prepared: list[PreparedCharacter] = []
        for reference in references[:3]:
            try:
                profile = self._profile(work_key, reference)
            except (RuntimeError, ValueError) as error:
                log_operation(
                    logger,
                    logging.WARNING,
                    feature="角色档案准备",
                    parameters={
                        "work_key": work_key,
                        "character_id": reference.character_id,
                        "reference_sha256": reference.sha256[:12],
                    },
                    result={"status": "failed", "error": type(error).__name__},
                )
                continue
            prepared.append(
                PreparedCharacter(
                    slot=len(prepared) + 1,
                    reference=reference,
                    profile=profile,
                )
            )
        if not prepared:
            raise RuntimeError("没有可用的角色档案")
        return prepared

    # 方法说明：读取或新建单角色档案并保存参考视图向量。
    def _profile(
        self,
        work_key: str,
        reference: CharacterReferenceAsset,
    ) -> CharacterProfile:
        started = time.perf_counter()
        reference_sha = self.repository.store_reference(reference)
        cache_key = profile_cache_key(
            work_key=work_key,
            character_id=reference.character_id,
            reference_sha256=reference_sha,
            model_revision=self.analyzer.model_revision,
            template_revision=PROFILE_TEMPLATE_REVISION,
        )
        cached = self.repository.load_profile(cache_key)
        if cached is not None:
            log_operation(
                logger,
                logging.INFO,
                feature="角色档案缓存读取",
                parameters={
                    "work_key": work_key,
                    "character_id": reference.character_id,
                    "reference_sha256": reference_sha[:12],
                },
                result={
                    "cache_hit": True,
                    "colors": len(cached.colors),
                    "profile_digest": cached.digest[:12],
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            return cached
        analysis = self.analyzer.analyze_profile(
            character_id=reference.character_id,
            display_name=reference.display_name,
            image_bytes=reference.image_bytes,
        )
        profile = CharacterProfile(
            work_key=work_key,
            character_id=reference.character_id,
            display_name=reference.display_name,
            provider=reference.provider,
            summary=reference.summary[:500],
            reference_sha256=reference_sha,
            stable_traits=analysis.stable_traits,
            outfit_traits=analysis.outfit_traits,
            colors=sample_profile_colors(reference.image_bytes, analysis.regions),
        )
        self.repository.save_profile(cache_key, profile)
        self.repository.save_embedding(
            work_key=work_key,
            character_id=reference.character_id,
            reference_sha256=reference_sha,
            revision=self.embedding_extractor.revision,
            vector=self.embedding_extractor.extract(reference.image_bytes),
        )
        log_operation(
            logger,
            logging.INFO,
            feature="角色档案构建",
            parameters={
                "work_key": work_key,
                "character_id": reference.character_id,
                "reference_sha256": reference_sha[:12],
                "model_revision": self.analyzer.model_revision,
            },
            result={
                "cache_hit": False,
                "stable_traits": len(profile.stable_traits),
                "outfit_traits": len(profile.outfit_traits),
                "colors": len(profile.colors),
                "profile_digest": profile.digest[:12],
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return profile

    # 方法说明：将已准备角色转换为 sidecar 候选输入。
    @staticmethod
    def _candidate(character: PreparedCharacter) -> dict[str, object]:
        return {
            "character_id": character.profile.character_id,
            "display_name": character.profile.display_name,
            "reference_slot": character.slot,
            "stable_traits": character.profile.stable_traits,
            "outfit_traits": character.profile.outfit_traits,
            "image_bytes": character.reference.image_bytes,
        }

    # 方法说明：过滤低置信度、重复框和错误角色槽位。
    def _validate_page_analysis(
        self,
        analysis: CharacterPageAnalysis,
        prepared: list[PreparedCharacter],
    ) -> CharacterPageAnalysis:
        confidence_threshold = max(
            self.min_confidence,
            PAGE_INSTANCE_CONFIDENCE_FLOOR,
        )
        raw_instances = sum(
            len(match.instances) for match in analysis.characters
        )
        allowed = {
            item.profile.character_id: item.slot
            for item in prepared
        }
        by_character = {item.character_id: item for item in analysis.characters}
        accepted_by_character: dict[str, list[object]] = {
            character_id: [] for character_id in allowed
        }
        candidates = [
            (character_id, instance)
            for character_id, slot in allowed.items()
            for match in [by_character.get(character_id)]
            if match is not None
            and match.reference_slot == slot
            and match.visible
            for instance in match.instances
            if instance.confidence >= confidence_threshold
        ]
        accepted: list[tuple[str, object]] = []
        for character_id, instance in sorted(
            candidates,
            key=lambda item: item[1].confidence,
            reverse=True,
        ):
            if any(
                other_id != character_id
                and _box_iou(instance.box_2d, other.box_2d) >= 0.65
                for other_id, other in accepted
            ):
                continue
            own_instances = accepted_by_character[character_id]
            if any(
                _box_overlap_ratio(instance.box_2d, other.box_2d)
                >= DUPLICATE_INSTANCE_OVERLAP_RATIO
                for other in own_instances
            ):
                continue
            own_instances.append(instance)
            accepted.append((character_id, instance))

        matches: list[PageCharacterMatch] = []
        for character_id, slot in allowed.items():
            match = by_character.get(character_id)
            instances = accepted_by_character[character_id]
            if match is None or match.reference_slot != slot or not instances:
                matches.append(
                    PageCharacterMatch(
                        character_id=character_id,
                        reference_slot=slot,
                        visible=False,
                        instances=[],
                    )
                )
                continue
            matches.append(
                PageCharacterMatch(
                    character_id=character_id,
                    reference_slot=slot,
                    visible=bool(instances),
                    outfit_matches_reference=(
                        match.outfit_matches_reference
                        and all(
                            instance.confidence >= 0.9
                            and not instance.counter_evidence
                            for instance in instances
                        )
                    ),
                    instances=instances,
                )
            )
        if not any(match.visible for match in matches):
            raise RuntimeError("当前页没有达到置信度门槛的已知角色")
        validated = CharacterPageAnalysis(
            characters=matches,
            unmatched_people=_deduplicate_unmatched(analysis.unmatched_people),
        )
        log_operation(
            logger,
            logging.INFO,
            feature="角色页面计划校验",
            parameters={
                "candidates": len(prepared),
                "raw_instances": raw_instances,
                "confidence_threshold": confidence_threshold,
            },
            result={
                "accepted_instances": sum(
                    len(match.instances) for match in validated.characters
                ),
                "visible_characters": sum(
                    1 for match in validated.characters if match.visible
                ),
                "unmatched_people": len(validated.unmatched_people),
            },
        )
        return validated

    # 方法说明：生成包含档案和实际 bbox 的页面上下文摘要。
    @staticmethod
    def _context_digest(
        prepared: list[PreparedCharacter],
        analysis: CharacterPageAnalysis,
    ) -> str:
        payload = json.dumps(
            {
                "profiles": [item.profile.digest for item in prepared],
                "analysis": analysis.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    # 方法说明：生成只包含有序角色档案的静态提示上下文摘要。
    @staticmethod
    def _profile_context_digest(prepared: list[PreparedCharacter]) -> str:
        payload = json.dumps(
            [item.profile.digest for item in prepared],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


# 方法说明：计算两个千分比矩形的交并比。
def _box_iou(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0


# 方法说明：计算交集占较小矩形面积的比例，用于删除重叠视图的重复实例。
def _box_overlap_ratio(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    smaller_area = min(left_area, right_area)
    return intersection / smaller_area if smaller_area else 0


# 方法说明：删除 VLM 输出的重复未匹配人物框。
def _deduplicate_unmatched(items):
    accepted = []
    for item in items:
        if any(_box_iou(item.box_2d, other.box_2d) >= 0.8 for other in accepted):
            continue
        accepted.append(item)
    return accepted
