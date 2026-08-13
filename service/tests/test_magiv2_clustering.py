from analysis_service.clustering import (
    propagate_cluster_matches,
    stable_cross_page_clusters,
)


def test_cross_page_clustering_is_stable_and_never_merges_same_page_groups():
    labels = [[0, 1], [0], [0]]
    embeddings = [
        [[1.0, 0.0], [0.0, 1.0]],
        [[0.99, 0.1]],
        [[0.995, 0.05]],
    ]

    clusters = stable_cross_page_clusters(
        labels,
        embeddings,
        max_distance=0.25,
    )

    assert clusters[0][0] == clusters[1][0] == clusters[2][0]
    assert clusters[0][1] != clusters[0][0]
    assert clusters[0][0] == "chapter-cluster-0"


def test_cross_page_clustering_uses_complete_linkage():
    labels = [[0], [0], [0]]
    embeddings = [
        [[1.0, 0.0]],
        [[0.995, 0.1]],
        [[0.955, 0.296]],
    ]

    clusters = stable_cross_page_clusters(
        labels,
        embeddings,
        max_distance=0.25,
    )

    assert clusters[0][0] == clusters[1][0]
    assert clusters[2][0] != clusters[0][0]


def test_cluster_propagation_requires_an_anchor_and_matching_candidate():
    matches = [
        [
            {
                "character_id": "anilist:277688",
                "character_name": "Elymas Edvan",
                "reference_url": "https://example/elymas.png",
                "portrait_reference_url": "https://example/elymas-face.png",
                "full_body_reference_url": "https://example/elymas-full.png",
                "status": "accepted",
                "confidence": 0.41,
                "best_distance": 0.59,
                "margin": 0.16,
                "reason": "",
            }
        ],
        [
            {
                "character_id": None,
                "character_name": "",
                "reference_url": None,
                "status": "rejected",
                "confidence": 0.0,
                "best_distance": 0.678,
                "margin": 0.16,
                "reason": "best candidate is too far",
            }
        ],
    ]
    candidates = [
        [
            {
                "character_id": "anilist:277688",
                "character_name": "Elymas Edvan",
                "reference_url": "https://example/elymas.png",
                "portrait_reference_url": "https://example/elymas-face.png",
                "full_body_reference_url": "https://example/elymas-full.png",
                "best_distance": 0.59,
                "margin": 0.16,
            }
        ],
        [
            {
                "character_id": "anilist:277688",
                "character_name": "Elymas Edvan",
                "reference_url": "https://example/elymas.png",
                "portrait_reference_url": "https://example/elymas-face.png",
                "full_body_reference_url": "https://example/elymas-full.png",
                "best_distance": 0.678,
                "margin": 0.16,
            }
        ],
    ]

    propagated = propagate_cluster_matches(
        matches,
        candidates,
        [["chapter-cluster-0"], ["chapter-cluster-0"]],
        max_distance=0.72,
        min_margin=0.08,
    )

    assert propagated[1][0]["status"] == "accepted"
    assert propagated[1][0]["character_id"] == "anilist:277688"
    assert propagated[1][0]["portrait_reference_url"] == (
        "https://example/elymas-face.png"
    )
    assert propagated[1][0]["full_body_reference_url"] == (
        "https://example/elymas-full.png"
    )
    assert propagated[1][0]["reason"] == "accepted by consistent cross-page cluster"


def test_cluster_conflict_rejects_every_member():
    matches = [
        [
            {
                "character_id": "anilist:277688",
                "status": "accepted",
                "confidence": 0.4,
            }
        ],
        [
            {
                "character_id": "anilist:344248",
                "status": "accepted",
                "confidence": 0.38,
            }
        ],
    ]
    candidates = [
        [
            {
                "character_id": "anilist:277688",
                "best_distance": 0.59,
                "margin": 0.16,
            }
        ],
        [
            {
                "character_id": "anilist:344248",
                "best_distance": 0.62,
                "margin": 0.14,
            }
        ],
    ]

    propagated = propagate_cluster_matches(
        matches,
        candidates,
        [["chapter-cluster-0"], ["chapter-cluster-0"]],
        max_distance=0.72,
        min_margin=0.08,
    )

    assert all(page[0]["status"] == "rejected" for page in propagated)
    assert all(page[0]["character_id"] is None for page in propagated)


def test_weak_conflicting_candidate_does_not_invalidate_accepted_anchor():
    matches = [
        [
            {
                "character_id": "anilist:277688",
                "character_name": "Elymas Edvan",
                "reference_url": "https://example/elymas.png",
                "status": "accepted",
                "confidence": 0.41,
            }
        ],
        [
            {
                "character_id": None,
                "character_name": "",
                "reference_url": None,
                "status": "rejected",
                "confidence": 0.0,
            }
        ],
    ]
    candidates = [
        [
            {
                "character_id": "anilist:277688",
                "character_name": "Elymas Edvan",
                "reference_url": "https://example/elymas.png",
                "best_distance": 0.59,
                "margin": 0.16,
            }
        ],
        [
            {
                "character_id": "anilist:344248",
                "character_name": "Luce Rubis",
                "reference_url": "https://example/luce.png",
                "best_distance": 0.68,
                "margin": 0.16,
            }
        ],
    ]

    propagated = propagate_cluster_matches(
        matches,
        candidates,
        [["chapter-cluster-0"], ["chapter-cluster-0"]],
        max_distance=0.72,
        min_margin=0.08,
    )

    assert propagated[0][0]["status"] == "accepted"
    assert propagated[0][0]["character_id"] == "anilist:277688"
    assert propagated[1][0]["status"] == "rejected"
    assert propagated[1][0]["character_id"] is None
