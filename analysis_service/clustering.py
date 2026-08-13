from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def stable_cross_page_clusters(
    page_labels: list[list[Any]],
    page_embeddings: list[list[list[float]]],
    *,
    max_distance: float,
) -> list[list[str]]:
    """Merge page-local MAGIv2 groups without ever merging two groups on one page."""
    if len(page_labels) != len(page_embeddings):
        raise ValueError("page labels and embeddings do not match")

    groups: list[dict[str, Any]] = []
    group_by_key: dict[tuple[int, str], int] = {}
    instance_groups: list[list[int]] = []
    flat_index = 0
    for page_index, (labels, embeddings) in enumerate(
        zip(page_labels, page_embeddings)
    ):
        if len(labels) != len(embeddings):
            raise ValueError("character labels and embeddings do not match")
        page_group_indexes: list[int] = []
        for character_index, (label, embedding) in enumerate(zip(labels, embeddings)):
            key = (page_index, _label_key(label, character_index))
            group_index = group_by_key.get(key)
            if group_index is None:
                group_index = len(groups)
                group_by_key[key] = group_index
                groups.append(
                    {
                        "page": page_index,
                        "first_instance": flat_index,
                        "embeddings": [],
                    }
                )
            groups[group_index]["embeddings"].append(embedding)
            page_group_indexes.append(group_index)
            flat_index += 1
        instance_groups.append(page_group_indexes)

    if not groups:
        return [[] for _ in page_labels]

    centroids = [_normalized_centroid(group["embeddings"]) for group in groups]
    distances = {
        (left, right): _distance(centroids[left], centroids[right])
        for left in range(len(groups))
        for right in range(left + 1, len(groups))
    }
    parent = list(range(len(groups)))
    members = {index: {index} for index in range(len(groups))}

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    candidates = sorted(
        (
            (distance, left, right)
            for (left, right), distance in distances.items()
            if groups[left]["page"] != groups[right]["page"]
            and distance <= max_distance
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    for _, left, right in candidates:
        left_root = root(left)
        right_root = root(right)
        if left_root == right_root:
            continue
        left_members = members[left_root]
        right_members = members[right_root]
        left_pages = {groups[index]["page"] for index in left_members}
        right_pages = {groups[index]["page"] for index in right_members}
        if left_pages.intersection(right_pages):
            continue
        if any(
            distances[_ordered_pair(a, b)] > max_distance
            for a in left_members
            for b in right_members
        ):
            continue
        keep, discard = sorted((left_root, right_root))
        parent[discard] = keep
        members[keep] = left_members | right_members
        del members[discard]

    clusters = sorted(
        members.values(),
        key=lambda indexes: min(groups[index]["first_instance"] for index in indexes),
    )
    cluster_id_by_group = {
        group_index: f"chapter-cluster-{cluster_index}"
        for cluster_index, group_indexes in enumerate(clusters)
        for group_index in group_indexes
    }
    return [
        [cluster_id_by_group[group_index] for group_index in group_indexes]
        for group_indexes in instance_groups
    ]


def propagate_cluster_matches(
    matches: list[list[dict[str, Any]]],
    candidates: list[list[dict[str, Any] | None]],
    cluster_ids: list[list[str]],
    *,
    max_distance: float,
    min_margin: float,
) -> list[list[dict[str, Any]]]:
    """Propagate only an unambiguous accepted identity inside a stable cluster."""
    if not (len(matches) == len(candidates) == len(cluster_ids)):
        raise ValueError("match pages do not align")

    members: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for page_index, ids in enumerate(cluster_ids):
        if not (len(matches[page_index]) == len(candidates[page_index]) == len(ids)):
            raise ValueError("match instances do not align")
        for character_index, cluster_id in enumerate(ids):
            members[cluster_id].append((page_index, character_index))

    for cluster_members in members.values():
        accepted_ids = {
            matches[page_index][character_index].get("character_id")
            for page_index, character_index in cluster_members
            if matches[page_index][character_index].get("status") == "accepted"
            and matches[page_index][character_index].get("character_id")
        }
        if len(accepted_ids) > 1:
            for page_index, character_index in cluster_members:
                match = matches[page_index][character_index]
                match.update(
                    {
                        "character_id": None,
                        "character_name": "",
                        "reference_url": None,
                        "status": "rejected",
                        "confidence": 0.0,
                        "reason": "cross-page cluster has conflicting candidates",
                    }
                )
            continue
        if len(accepted_ids) != 1:
            continue

        accepted_id = next(iter(accepted_ids))
        for page_index, character_index in cluster_members:
            match = matches[page_index][character_index]
            candidate = candidates[page_index][character_index]
            if match.get("status") == "accepted" or candidate is None:
                continue
            if candidate.get("character_id") != accepted_id or not _eligible(
                candidate,
                max_distance=max_distance,
                min_margin=min_margin,
            ):
                continue
            best_distance = float(candidate["best_distance"])
            match.update(
                {
                    "character_id": candidate["character_id"],
                    "character_name": candidate["character_name"],
                    "reference_url": candidate["reference_url"],
                    "status": "accepted",
                    "confidence": max(0.0, min(1.0, (1.0 - best_distance) * 0.85)),
                    "reason": "accepted by consistent cross-page cluster",
                }
            )
    return matches


def _eligible(candidate: dict[str, Any], *, max_distance: float, min_margin: float) -> bool:
    return bool(
        candidate.get("character_id")
        and float(candidate.get("best_distance", math.inf)) <= max_distance
        and float(candidate.get("margin", 0.0)) >= min_margin
    )


def _label_key(label: Any, character_index: int) -> str:
    if hasattr(label, "item"):
        label = label.item()
    return str(label) if label is not None else f"instance-{character_index}"


def _normalized_centroid(embeddings: list[list[float]]) -> list[float]:
    if not embeddings or not embeddings[0]:
        raise ValueError("character embedding must not be empty")
    width = len(embeddings[0])
    if any(len(embedding) != width for embedding in embeddings):
        raise ValueError("character embedding dimensions do not match")
    centroid = [sum(values) / len(embeddings) for values in zip(*embeddings)]
    norm = math.sqrt(sum(value * value for value in centroid))
    if norm == 0:
        raise ValueError("character embedding has zero norm")
    return [value / norm for value in centroid]


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _ordered_pair(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left < right else (right, left)
