from __future__ import annotations

from typing import Any, Iterable


def ranked_identity_candidates(
    distances: Iterable[float],
    bank: list[Any],
) -> list[tuple[float, int]]:
    """Return the best reference view for each logical character."""
    best_by_character: dict[str, tuple[float, int]] = {}
    for index, value in enumerate(distances):
        character_id = str(bank[index].character_id)
        candidate = (float(value), index)
        current = best_by_character.get(character_id)
        if current is None or candidate < current:
            best_by_character[character_id] = candidate
    return sorted(best_by_character.values())
