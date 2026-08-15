"""导出独立角色库、档案构建和轻量检索能力。"""

from .builder import CharacterLibraryBuilder
from .embeddings import ImageEmbeddingExtractor, cosine_similarity
from .models import (
    CharacterColorEvidence,
    CharacterPageContext,
    CharacterPromptContext,
    CharacterProfile,
    CharacterReferenceAsset,
    PreparedCharacter,
)
from .repository import CharacterLibraryRepository
from .retrieval import CharacterRetrievalResult, CharacterRetriever

__all__ = [
    "CharacterColorEvidence",
    "CharacterLibraryBuilder",
    "CharacterLibraryRepository",
    "CharacterPageContext",
    "CharacterPromptContext",
    "CharacterProfile",
    "CharacterReferenceAsset",
    "CharacterRetrievalResult",
    "CharacterRetriever",
    "ImageEmbeddingExtractor",
    "PreparedCharacter",
    "cosine_similarity",
]
