from .matching import (
    alias_title_matches,
    normalize_character_name,
    normalize_title,
)
from .models import CharacterIdentityEntry, WorkIdentityEntry
from .registry import WorkIdentityRegistry


__all__ = [
    "CharacterIdentityEntry",
    "WorkIdentityEntry",
    "WorkIdentityRegistry",
    "alias_title_matches",
    "normalize_character_name",
    "normalize_title",
]
