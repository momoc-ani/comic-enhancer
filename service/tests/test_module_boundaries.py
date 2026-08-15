from comic_enhancer.api.app import create_app
from comic_enhancer.api.routes.metadata import resolve_metadata
from comic_enhancer.api.routes.pages import process_page
from comic_enhancer.api.routes.results import result
from comic_enhancer.api.routes.system import capabilities
from comic_enhancer.application import (
    ProcessingService,
    ReferenceBankService,
)
from comic_enhancer.domain import (
    MetadataResolution,
    ProcessingMode,
    WorkIdentity,
)
from comic_enhancer.identities import WorkIdentityRegistry
from comic_enhancer.jobs import ProcessingService as CompatibleProcessingService
from comic_enhancer.main import create_app as compatible_create_app
from comic_enhancer.metadata import (
    AniListProvider,
    BangumiProvider,
    JikanMALProvider,
    KitsuProvider,
    MangaUpdatesProvider,
    MetadataAggregator,
    ShikimoriProvider,
)
from comic_enhancer.models import WorkIdentity as CompatibleWorkIdentity
from comic_enhancer.references import ReferenceImageStore, assess_reference_image
from comic_enhancer.storage import ResultCache


# 方法说明：验证数据能力类和函数分别归属其职责模块。
def test_data_capabilities_are_split_into_responsibility_modules():
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


# 方法说明：验证领域模型按业务主题拆分且旧导入路径保持兼容。
def test_domain_models_are_split_by_business_topic():
    assert WorkIdentity.__module__ == "comic_enhancer.domain.identity"
    assert ProcessingMode.__module__ == "comic_enhancer.domain.processing"
    assert MetadataResolution.__module__ == "comic_enhancer.domain.metadata"
    assert CompatibleWorkIdentity is WorkIdentity


# 方法说明：验证应用编排服务不再由旧集中模块承载。
def test_application_services_have_independent_modules():
    assert ProcessingService.__module__ == "comic_enhancer.application.processing"
    assert ReferenceBankService.__module__ == (
        "comic_enhancer.application.reference_bank"
    )
    assert CompatibleProcessingService is ProcessingService


# 方法说明：验证 API 路由按资源拆分并保留 ASGI 兼容入口。
def test_api_routes_are_split_by_resource():
    assert capabilities.__module__ == "comic_enhancer.api.routes.system"
    assert process_page.__module__ == "comic_enhancer.api.routes.pages"
    assert resolve_metadata.__module__ == "comic_enhancer.api.routes.metadata"
    assert result.__module__ == "comic_enhancer.api.routes.results"
    assert compatible_create_app is create_app
