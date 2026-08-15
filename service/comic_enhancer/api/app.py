from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..adapters import AdapterRegistry, GiteeAdapterStore
from ..application import (
    ProcessingService,
    ReferenceBankService,
    RemoteAdapterService,
)
from ..config import Settings, load_settings
from ..identities import WorkIdentityRegistry
from ..inference import InferenceBackend, create_backend
from ..inference.comfyui import PresetWorkflowLoader
from ..metadata import (
    AniListProvider,
    BangumiProvider,
    JikanMALProvider,
    KitsuProvider,
    MangaUpdatesProvider,
    MetadataAggregator,
    ShikimoriProvider,
)
from ..references import ReferenceImageStore
from ..storage import ResultCache
from .context import ApplicationContext
from .routes import (
    adapters_router,
    metadata_router,
    pages_router,
    results_router,
    system_router,
)

logger = logging.getLogger(__name__)


def _create_backend(settings: Settings) -> InferenceBackend:
    """按配置创建统一推理后端。"""
    backend_options: dict[str, object] = {
        "realcugan_enabled": settings.realcugan_enabled,
        "realcugan_resource_root": settings.realcugan_resource_root,
        "realcugan_timeout_seconds": settings.realcugan_timeout_seconds,
    }
    if settings.backend == "comfyui":
        workflow_loader = PresetWorkflowLoader(
            fast_workflow=settings.comfyui_workflow_fast,
            quality_workflow=settings.comfyui_workflow_quality,
            workflow_root=settings.comfyui_workflow_root,
            cobra_workflow=settings.comfyui_workflow_cobra,
            flux2_workflow=settings.comfyui_workflow_flux2,
            flux2_quant_workflow=settings.comfyui_workflow_flux2_quant,
        )
        backend_options.update(
            {
                "base_url": settings.comfyui_url,
                "cobra_enabled": settings.comfyui_cobra_enabled,
                "cobra_workflow": settings.comfyui_workflow_cobra,
                "cobra_reference_limit": settings.cobra_reference_limit,
                "flux2_enabled": settings.comfyui_flux2_enabled,
                "flux2_workflow": settings.comfyui_workflow_flux2,
                "flux2_reference_limit": settings.flux2_reference_limit,
                "flux2_quant_enabled": settings.comfyui_flux2_quant_enabled,
                "flux2_quant_workflow": settings.comfyui_workflow_flux2_quant,
                "timeout_seconds": settings.comfyui_timeout_seconds,
                "poll_interval_seconds": settings.comfyui_poll_interval_seconds,
                "workflow_loader": workflow_loader,
            }
        )
    return create_backend(settings.backend, **backend_options)


def _create_metadata(settings: Settings) -> MetadataAggregator:
    """创建元数据聚合器和独立提供方。"""
    return MetadataAggregator(
        settings.runtime_dir / "metadata",
        enabled=settings.metadata_enabled,
        ttl_seconds=settings.metadata_ttl_seconds,
        providers=[
            BangumiProvider(timeout_seconds=settings.metadata_timeout_seconds),
            AniListProvider(timeout_seconds=settings.metadata_timeout_seconds),
            KitsuProvider(timeout_seconds=settings.metadata_timeout_seconds),
            ShikimoriProvider(timeout_seconds=settings.metadata_timeout_seconds),
            JikanMALProvider(timeout_seconds=settings.metadata_timeout_seconds),
            MangaUpdatesProvider(
                api_url=settings.mangaupdates_api_url,
                api_token=settings.mangaupdates_api_token,
                timeout_seconds=settings.metadata_timeout_seconds,
            ),
        ],
    )


def _create_context(settings: Settings) -> ApplicationContext:
    """组装应用服务及其基础设施依赖。"""
    backend = _create_backend(settings)
    registry = AdapterRegistry(
        settings.adapter_index,
        settings.generic_adapter_id,
        settings.adapter_weights_root,
    )
    cache = ResultCache(settings.runtime_dir / "results")
    references = ReferenceImageStore(settings.runtime_dir / "references")
    metadata = _create_metadata(settings)
    identities = WorkIdentityRegistry(settings.work_identity_index)
    processor = ProcessingService(
        registry=registry,
        cache=cache,
        backend=backend,
        semaphore=asyncio.Semaphore(settings.max_parallel_inference),
    )
    gitee_store = None
    remote_adapters = None
    if settings.gitee_enabled:
        gitee_store = GiteeAdapterStore(
            api_url=settings.gitee_api_url,
            owner=settings.gitee_owner,
            repo=settings.gitee_repo,
            branch=settings.gitee_branch,
            token=settings.gitee_token,
            index_path=settings.gitee_index_path,
            release_tag=settings.gitee_release_tag,
            timeout_seconds=settings.gitee_timeout_seconds,
        )
        remote_adapters = RemoteAdapterService(
            store=gitee_store,
            registry=registry,
            backend=backend,
            index_path=settings.adapter_index,
            weights_root=settings.adapter_weights_root,
        )
    return ApplicationContext(
        settings=settings,
        backend=backend,
        registry=registry,
        cache=cache,
        references=references,
        metadata=metadata,
        identities=identities,
        processor=processor,
        reference_bank=ReferenceBankService(references, identities),
        gitee_store=gitee_store,
        remote_adapters=remote_adapters,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 Comic Enhancer FastAPI 应用。"""
    context = _create_context(settings or load_settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """管理应用启动和关闭期间的后台资源。"""
        context.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        if context.remote_adapters is not None:
            try:
                await context.remote_adapters.sync()
            except Exception as error:  # 启动失败时继续使用本地缓存索引。
                logger.warning("Gitee 索引同步失败，将继续使用本地索引: %s", error)
        yield

    app = FastAPI(title="Comic Enhancer", version=__version__, lifespan=lifespan)
    app.state.context = context
    app.state.settings = context.settings
    app.state.processor = context.processor
    app.state.gitee_store = context.gitee_store
    app.state.metadata = context.metadata
    app.state.identities = context.identities
    app.state.references = context.references
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(system_router)
    app.include_router(pages_router)
    app.include_router(metadata_router)
    app.include_router(adapters_router)
    app.include_router(results_router)
    return app
