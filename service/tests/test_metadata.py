from pathlib import Path

import httpx

from comic_enhancer.metadata import AniListProvider, BangumiProvider, KitsuProvider, MangaUpdatesProvider, MetadataAggregator, _confidence
from comic_enhancer.models import WorkIdentity


def work(**values):
    defaults = {
        "source": "copy_manga",
        "source_work_id": "one-piece",
        "title": "One Piece",
        "author": "Oda",
    }
    defaults.update(values)
    return WorkIdentity(**defaults)


def test_bangumi_maps_cover_and_character_summary(monkeypatch):
    responses = {
        "https://api.bgm.tv/v0/search/subjects": httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 3510,
                        "platform": "漫画",
                        "name": "ONE PIECE",
                        "name_cn": "航海王",
                        "images": {"large": "https://lain.bgm.tv/cover.jpg"},
                    }
                ]
            },
        ),
        "https://api.bgm.tv/v0/subjects/3510": httpx.Response(
            200,
            json={
                "id": 3510,
                "name": "ONE PIECE",
                "name_cn": "航海王",
                "summary": "summary",
                "images": {"large": "https://lain.bgm.tv/cover.jpg"},
                "infobox": [{"key": "作者", "value": "尾田荣一郎"}],
            },
        ),
        "https://api.bgm.tv/v0/subjects/3510/characters": httpx.Response(
            200,
            json=[
                {
                    "id": 2358,
                    "name": "蒙奇·D·路飞",
                    "summary": "主角介绍",
                    "relation": "主角",
                    "images": {"large": "https://lain.bgm.tv/character.jpg"},
                }
            ],
        ),
    }

    def request(self, method, url, **kwargs):
        response = responses[url]
        response.request = httpx.Request(method, url)
        return response

    monkeypatch.setattr(httpx.Client, "request", request)
    result = BangumiProvider().search(work())

    assert result is not None
    assert result.provider_id == "3510"
    assert result.cover_url == "https://lain.bgm.tv/cover.jpg"
    assert result.characters[0].summary == "主角介绍"


def test_bangumi_prefers_series_alias_over_numbered_volume():
    selected = BangumiProvider._select(
        [
            {
                "id": 12121,
                "platform": "漫画",
                "name": "ONE PIECE (1)",
                "name_cn": "",
                "series": False,
            },
            {
                "id": 3510,
                "platform": "漫画",
                "name": "ONE PIECE",
                "name_cn": "航海王",
                "series": True,
            },
        ],
        work(),
    )

    assert selected is not None
    assert selected["id"] == 3510


def test_anilist_and_kitsu_map_titles(monkeypatch):
    def post(self, url, **kwargs):
        response = httpx.Response(
            200,
            json={
                "data": {
                    "Page": {
                        "media": [
                            {
                                "id": 100,
                                "title": {"romaji": "ONE PIECE", "userPreferred": "ONE PIECE"},
                                "synonyms": [],
                                "description": "desc",
                                "coverImage": {"large": "https://anilist.co/cover.jpg"},
                                "staff": {"edges": []},
                                "characters": {"edges": []},
                            }
                        ]
                    }
                }
            },
        )
        response.request = httpx.Request("POST", url)
        return response

    def get(self, url, **kwargs):
        response = httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "38",
                        "attributes": {
                            "canonicalTitle": "One Piece",
                            "titles": {"en": "One Piece"},
                            "synopsis": "desc",
                            "posterImage": {"large": "https://kitsu.io/cover.jpg"},
                        },
                    }
                ]
            },
        )
        response.request = httpx.Request("GET", url)
        return response

    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr(httpx.Client, "get", get)

    assert AniListProvider().search(work()).cover_url == "https://anilist.co/cover.jpg"
    assert KitsuProvider().search(work()).provider_id == "38"


def test_anilist_exact_id_does_not_send_title_search(monkeypatch):
    captured = {}

    def post(self, url, **kwargs):
        captured.update(kwargs["json"]["variables"])
        response = httpx.Response(
            200,
            json={
                "data": {
                    "Page": {
                        "media": [
                            {
                                "id": 150193,
                                "title": {
                                    "romaji": "Tsuihou sareta Tensei Juukishi wa Game Chishiki de Musou suru",
                                    "native": "追放された転生重騎士はゲーム知識で無双する",
                                    "userPreferred": "Tsuihou sareta Tensei Juukishi wa Game Chishiki de Musou suru",
                                },
                                "synonyms": [],
                                "description": "",
                                "coverImage": {},
                                "staff": {"edges": []},
                                "characters": {"edges": []},
                            }
                        ]
                    }
                }
            },
        )
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx.Client, "post", post)

    result = AniListProvider().search(
        work(external_ids={"anilist": "150193"})
    )

    assert result.provider_id == "150193"
    assert captured == {"id": 150193}


def test_aggregator_caches_provider_failures(tmp_path: Path):
    class BrokenProvider:
        name = "broken"

        def search(self, _work):
            raise httpx.ConnectError("offline")

    aggregator = MetadataAggregator(tmp_path, providers=[BrokenProvider()])
    first = aggregator.resolve(work())
    second = aggregator.resolve(work())

    assert first.selected is None
    assert first.errors == {"broken": "offline"}
    assert second.errors == first.errors


def test_aggregator_prefers_provider_order_after_confidence_threshold(tmp_path: Path):
    class Provider:
        def __init__(self, name, confidence, cover):
            self.name = name
            self.confidence = confidence
            self.cover = cover

        def search(self, item):
            from comic_enhancer.models import WorkMetadata

            return WorkMetadata(
                provider=self.name,
                provider_id=self.name,
                title=item.title,
                cover_url=self.cover,
                confidence=self.confidence,
            )

    result = MetadataAggregator(
        tmp_path,
        providers=[
            Provider("bangumi", 0.65, "https://bgm/cover.jpg"),
            Provider("anilist", 0.95, "https://anilist/cover.jpg"),
        ],
    ).resolve(work())

    assert result.selected is not None
    assert result.selected.provider == "bangumi"


def test_mangaupdates_maps_public_search_record(monkeypatch):
    def post(self, url, **kwargs):
        response = httpx.Response(
            200,
            json={
                "results": [
                    {
                        "record": {
                            "series_id": 123,
                            "title": "One Piece",
                            "description": "summary",
                            "url": "https://www.mangaupdates.com/series/one-piece",
                            "image": {"url": {"original": "https://cdn.mangaupdates.com/cover.jpg"}},
                        }
                    }
                ]
            },
        )
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx.Client, "post", post)
    result = MangaUpdatesProvider().search(work())

    assert result is not None
    assert result.provider_id == "123"
    assert result.cover_url == "https://cdn.mangaupdates.com/cover.jpg"


def test_empty_title_never_produces_reference_confidence():
    assert _confidence("", work()) == 0
    assert _confidence("One Piece", work(title="")) == 0
