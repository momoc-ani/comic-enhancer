from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def encode_binary_mask(mask: Any) -> list[int]:
    flat = mask.reshape(-1) if hasattr(mask, "reshape") else _flatten(mask)
    counts: list[int] = []
    expected = 0
    run = 0
    for value in flat:
        pixel = int(value != 0)
        if pixel == expected:
            run += 1
            continue
        counts.append(run)
        run = 1
        expected = pixel
    counts.append(run)
    return counts


def _flatten(values: Iterable[Any]):
    for value in values:
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            yield from _flatten(value)
        else:
            yield value
