from dataclasses import dataclass

from analysis_service.matching import ranked_identity_candidates


@dataclass
class Entry:
    character_id: str


def test_ranked_identity_candidates_uses_best_view_per_character():
    bank = [
        Entry("elymas"),
        Entry("elymas"),
        Entry("luce"),
        Entry("maris"),
    ]

    ranked = ranked_identity_candidates([0.71, 0.59, 0.82, 0.77], bank)

    assert ranked == [(0.59, 1), (0.77, 3), (0.82, 2)]


def test_same_character_second_view_does_not_reduce_identity_margin():
    bank = [Entry("elymas"), Entry("elymas"), Entry("luce")]

    ranked = ranked_identity_candidates([0.59, 0.60, 0.75], bank)

    assert ranked[0] == (0.59, 0)
    assert ranked[1] == (0.75, 2)
