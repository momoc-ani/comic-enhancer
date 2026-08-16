from pathlib import Path

import httpx

from comic_enhancer.metadata import AniListProvider, BangumiProvider, KitsuProvider, MangaUpdatesProvider, MetadataAggregator, _confidence
from comic_enhancer.models import CharacterReference, WorkIdentity, WorkMetadata


# 方法说明：创建测试使用的作品身份数据。
def work(**values):
    defaults = {
        "source": "copy_manga",
        "source_work_id": "one-piece",
        "title": "One Piece",
        "author": "Oda",
    }
    defaults.update(values)
    return WorkIdentity(**defaults)


# 方法说明：验证 Bangumi 数据会映射封面和角色摘要。
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

    # 方法说明：模拟测试中的 HTTP 请求。
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


# 方法说明：验证 Bangumi 会优先匹配系列别名而非分卷标题。
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


# 方法说明：验证 AniList 和 Kitsu 标题字段会正确映射。
def test_anilist_and_kitsu_map_titles(monkeypatch):
    # 方法说明：模拟测试中的 HTTP POST 请求。
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

    # 方法说明：读取指定数据或模拟测试中的 GET 请求。
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


# 方法说明：验证 AniList 精确 ID 查询不会同时发送标题搜索。
def test_anilist_exact_id_does_not_send_title_search(monkeypatch):
    captured = {}

    # 方法说明：模拟测试中的 HTTP POST 请求。
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


# 方法说明：验证元数据聚合器会缓存提供方失败结果。
def test_aggregator_caches_provider_failures(tmp_path: Path):
    class BrokenProvider:
        name = "broken"

        # 方法说明：查询并转换当前提供方的作品元数据。
        def search(self, _work):
            raise httpx.ConnectError("offline")

    aggregator = MetadataAggregator(tmp_path, providers=[BrokenProvider()])
    first = aggregator.resolve(work())
    second = aggregator.resolve(work())

    assert first.selected is None
    assert first.errors == {"broken": "offline"}
    assert second.errors == first.errors


# 方法说明：验证不同外部 ID 会生成不同元数据缓存键。
def test_aggregator_cache_key_changes_with_external_ids(tmp_path: Path):
    aggregator = MetadataAggregator(tmp_path, providers=[])
    manga_entry = work(external_ids={"bangumi": "418302"})
    anime_entry = work(external_ids={"bangumi": "511177"})

    assert aggregator._cache_path(manga_entry) != aggregator._cache_path(anime_entry)


# 方法说明：验证聚合器优先使用最接近规范标题的站点别名获取角色。
def test_aggregator_uses_work_alias_to_resolve_characters(tmp_path: Path):
    class AliasProvider:
        name = "bangumi"

        # 方法说明：初始化查询记录。
        def __init__(self):
            self.queries = []

        # 方法说明：仅对简体作品别名返回带角色的正确候选。
        def search(self, item):
            self.queries.append(item.title)
            if item.title != "剑姬神圣谭":
                return WorkMetadata(
                    provider=self.name,
                    provider_id="wrong",
                    title="剑姬怒放",
                    confidence=0.45,
                )
            return WorkMetadata(
                provider=self.name,
                provider_id="130544",
                title="期待在地下城邂逅有错吗 外传 剑姬神圣谭",
                confidence=0.65,
                characters=[
                    CharacterReference(
                        provider=self.name,
                        provider_id="1",
                        name="艾丝·华伦斯坦",
                        image_url="https://example.com/ais.jpg",
                    )
                ],
            )

    provider = AliasProvider()
    result = MetadataAggregator(tmp_path, providers=[provider]).resolve(
        work(
            title="劍姬神聖譚",
            title_aliases=["剑姬神圣谭", "剑姬"],
        )
    )

    assert provider.queries == ["剑姬神圣谭"]
    assert result.selected is not None
    assert result.selected.provider_id == "130544"
    assert len(result.selected.characters) == 1


# 方法说明：验证达到置信度门槛后按提供方顺序选择候选。
def test_aggregator_prefers_provider_order_after_confidence_threshold(tmp_path: Path):
    class Provider:
        # 方法说明：初始化当前对象及其运行状态。
        def __init__(self, name, confidence, cover):
            self.name = name
            self.confidence = confidence
            self.cover = cover

        # 方法说明：查询并转换当前提供方的作品元数据。
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


# 方法说明：验证 MangaUpdates 公共搜索记录会映射为统一元数据。
def test_mangaupdates_maps_public_search_record(monkeypatch):
    # 方法说明：模拟测试中的 HTTP POST 请求。
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


# 方法说明：验证空标题不会产生参考图匹配置信度。
def test_empty_title_never_produces_reference_confidence():
    assert _confidence("", work()) == 0
    assert _confidence("One Piece", work(title="")) == 0
