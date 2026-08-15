from comic_enhancer.adapters import AdapterRegistry, GiteeAdapterStore
from comic_enhancer.identities import WorkIdentityRegistry
from comic_enhancer.metadata import (
    AniListProvider,
    BangumiProvider,
    JikanMALProvider,
    KitsuProvider,
    MangaUpdatesProvider,
    MetadataAggregator,
    ShikimoriProvider,
)
from comic_enhancer.references import ReferenceImageStore, assess_reference_image
from comic_enhancer.storage import ResultCache


# 方法说明：验证数据能力类和函数分别归属其职责模块。
def test_data_capabilities_are_split_into_responsibility_modules():
    assert AdapterRegistry.__module__ == "comic_enhancer.adapters.registry"
    assert GiteeAdapterStore.__module__ == "comic_enhancer.adapters.gitee"
    assert WorkIdentityRegistry.__module__ == "comic_enhancer.identities.registry"
    assert MetadataAggregator.__module__ == "comic_enhancer.metadata.aggregator"
    assert ReferenceImageStore.__module__ == "comic_enhancer.references.store"
    assert assess_reference_image.__module__ == "comic_enhancer.references.quality"
    assert ResultCache.__module__ == "comic_enhancer.storage.result_cache"


# 方法说明：验证每个元数据提供方都有独立实现模块。
def test_metadata_providers_have_independent_implementation_modules():
    providers = {
        BangumiProvider: "bangumi",
        AniListProvider: "anilist",
        KitsuProvider: "kitsu",
        ShikimoriProvider: "shikimori",
        JikanMALProvider: "jikan",
        MangaUpdatesProvider: "mangaupdates",
    }
    for provider, module_name in providers.items():
        assert provider.__module__ == (
            f"comic_enhancer.metadata.providers.{module_name}"
        )
