import json

from comic_enhancer.identities import WorkIdentityRegistry
from comic_enhancer.models import WorkIdentity


def registry(tmp_path):
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "works": [
                    {
                        "identity_id": "heavy-knight",
                        "title_aliases": [
                            "被追放的轉生重騎士用遊戲知識開無雙",
                            "追放された転生重騎士はゲーム知識で無双する",
                        ],
                        "external_ids": {
                            "bangumi": "418302",
                            "anilist": "150193",
                        },
                        "characters": [
                            {
                                "identity_id": "elymas-edvan",
                                "name": "Elymas Edvan",
                                "aliases": ["エルマ・エドヴァン"],
                                "external_ids": {
                                    "bangumi": "173007",
                                    "anilist": "277688",
                                },
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return WorkIdentityRegistry(path)


def work(title, external_ids=None):
    return WorkIdentity(
        source="copy_manga",
        source_work_id="chapter-work",
        title=title,
        external_ids=external_ids or {},
    )


def test_registry_enriches_long_alias_with_site_suffix(tmp_path):
    enriched = registry(tmp_path).enrich(
        work("被追放的轉生重騎士用遊戲知識開無雙 - 拷貝漫畫")
    )

    assert enriched.external_ids == {
        "bangumi": "418302",
        "anilist": "150193",
    }


def test_registry_preserves_explicit_external_id(tmp_path):
    enriched = registry(tmp_path).enrich(
        work(
            "被追放的轉生重騎士用遊戲知識開無雙",
            {"anilist": "explicit"},
        )
    )

    assert enriched.external_ids == {
        "bangumi": "418302",
        "anilist": "explicit",
    }


def test_registry_does_not_match_partial_unrelated_title(tmp_path):
    enriched = registry(tmp_path).enrich(work("轉生重騎士"))

    assert enriched.external_ids == {}


def test_registry_merges_cross_provider_character_aliases(tmp_path):
    from comic_enhancer.models import CharacterReference

    item = work("被追放的轉生重騎士用遊戲知識開無雙")
    bangumi = CharacterReference(
        provider="bangumi",
        provider_id="173007",
        name="エルマ・エドヴァン",
    )
    anilist = CharacterReference(
        provider="anilist",
        provider_id="277688",
        name="Elymas Edvan",
    )

    assert registry(tmp_path).canonical_character(item, bangumi) == (
        "work:heavy-knight:elymas-edvan",
        "Elymas Edvan",
    )
    assert registry(tmp_path).canonical_character(item, anilist) == (
        "work:heavy-knight:elymas-edvan",
        "Elymas Edvan",
    )
