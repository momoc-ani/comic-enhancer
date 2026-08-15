from __future__ import annotations

from dataclasses import dataclass

from .embeddings import ImageEmbeddingExtractor, cosine_similarity
from .repository import CharacterLibraryRepository


@dataclass(frozen=True)
class CharacterRetrievalResult:
    """记录一个向量召回候选及其相似度。"""

    character_id: str
    reference_sha256: str
    score: float


class CharacterRetriever:
    """仅在当前作品内召回角色候选，身份最终由 VLM 决定。"""

    # 方法说明：初始化轻量向量提取器和角色库仓储。
    def __init__(
        self,
        repository: CharacterLibraryRepository,
        extractor: ImageEmbeddingExtractor | None = None,
    ):
        self.repository = repository
        self.extractor = extractor or ImageEmbeddingExtractor()

    # 方法说明：按余弦相似度返回当前作品内的前若干候选。
    def retrieve(
        self,
        *,
        work_key: str,
        image_bytes: bytes,
        limit: int = 6,
    ) -> list[CharacterRetrievalResult]:
        query = self.extractor.extract(image_bytes)
        matches = [
            CharacterRetrievalResult(
                character_id=character_id,
                reference_sha256=reference_sha256,
                score=cosine_similarity(query, vector),
            )
            for character_id, reference_sha256, vector in self.repository.load_embeddings(
                work_key,
                self.extractor.revision,
            )
        ]
        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[: max(1, limit)]
