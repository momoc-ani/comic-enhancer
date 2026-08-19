from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..character_library import CharacterLibraryBuilder, CharacterLibraryRepository
from ..character_vision import LlamaCppCharacterVisionAnalyzer
from ..application import (
    PregenerationService,
    PriorityInferenceGate,
    ProcessingService,
    ReferenceBankService,
)
from ..config import Settings, load_settings
from ..identities import WorkIdentityRegistry
from ..inference import InferenceBackend, create_backend
from ..inference.comfyui import PresetWorkflowLoader
from ..logging_utils import log_operation
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
from ..storage import PregenerationStore, ResultCache
from ..transport import GZipRequestMiddleware, GZipResponseMiddleware
from .context import ApplicationContext
from .routes import (
    metadata_router,
    pages_router,
    pregeneration_router,
    results_router,
    system_router,
)


logger = logging.getLogger(__name__)


# 方法说明：让业务 INFO 日志复用 Uvicorn 的终端处理器和格式。
def _configure_application_logging() -> None:
    application_logger = logging.getLogger("comic_enhancer")
    application_logger.setLevel(logging.INFO)
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_logger = logging.getLogger("uvicorn")
    handlers = uvicorn_error_logger.handlers or uvicorn_logger.handlers
    if handlers:
        application_logger.handlers = list(handlers)
        application_logger.propagate = False
    else:
        application_logger.propagate = True

def _create_backend(settings: Settings) -> InferenceBackend:
    """按配置创建统一推理后端。"""
    backend_options: dict[str, object] = {
        "realcugan_enabled": settings.realcugan_enabled,
        "realcugan_resource_root": settings.realcugan_resource_root,
        "realcugan_timeout_seconds": settings.realcugan_timeout_seconds,
    }
    if settings.backend == "comfyui":
        character_library = None
        if (
            settings.comfyui_flux2_character_enabled
            or settings.comfyui_flux2_character_lineart_enabled
        ):
            character_library_root = (
                settings.character_library_root
                or settings.runtime_dir / "character-library"
            )
            character_analyzer = LlamaCppCharacterVisionAnalyzer(
                base_url=settings.qwen_vl_base_url,
                api_key=settings.qwen_vl_api_key,
                model_id=settings.qwen_vl_model_id,
                deployment_revision=settings.qwen_vl_deployment_revision,
                timeout_seconds=settings.qwen_vl_timeout_seconds,
                max_image_edge=settings.qwen_vl_max_image_edge,
            )
            character_library = CharacterLibraryBuilder(
                repository=CharacterLibraryRepository(character_library_root),
                analyzer=character_analyzer,
                min_confidence=settings.character_min_confidence,
            )
        workflow_loader = PresetWorkflowLoader(
            fast_workflow=settings.comfyui_workflow_fast,
            quality_workflow=settings.comfyui_workflow_quality,
            flux2_workflow=settings.comfyui_workflow_flux2,
            flux2_quant_workflow=settings.comfyui_workflow_flux2_quant,
            flux2_character_workflow=settings.comfyui_workflow_flux2_character,
            flux2_character_no_reference_workflow=(
                settings.comfyui_workflow_flux2_character_no_reference
            ),
            flux2_character_lineart_workflow=(
                settings.comfyui_workflow_flux2_character_lineart
            ),
            flux2_character_lineart_no_reference_workflow=(
                settings.comfyui_workflow_flux2_character_lineart_no_reference
            ),
            flux2_9b_lora_workflow=settings.comfyui_workflow_flux2_9b_lora,
            flux2_9b_fast_workflow=settings.comfyui_workflow_flux2_9b_fast,
            flux2_9b_fast_lowres_workflow=(
                settings.comfyui_workflow_flux2_9b_fast_lowres
            ),
            flux2_4b_source_workflow=settings.comfyui_workflow_flux2_4b_source,
            flux2_4b_color_workflow=settings.comfyui_workflow_flux2_4b_color,
        )
        backend_options.update(
            {
                "base_url": settings.comfyui_url,
                "flux2_enabled": settings.comfyui_flux2_enabled,
                "flux2_workflow": settings.comfyui_workflow_flux2,
                "flux2_reference_limit": settings.flux2_reference_limit,
                "flux2_quant_enabled": settings.comfyui_flux2_quant_enabled,
                "flux2_quant_workflow": settings.comfyui_workflow_flux2_quant,
                "flux2_character_enabled": settings.comfyui_flux2_character_enabled,
                "flux2_character_workflow": settings.comfyui_workflow_flux2_character,
                "flux2_character_no_reference_workflow": (
                    settings.comfyui_workflow_flux2_character_no_reference
                ),
                "flux2_character_native_resolution": (
                    settings.comfyui_flux2_character_native_resolution
                ),
                "flux2_character_lineart_enabled": (
                    settings.comfyui_flux2_character_lineart_enabled
                ),
                "flux2_character_lineart_workflow": (
                    settings.comfyui_workflow_flux2_character_lineart
                ),
                "flux2_character_lineart_no_reference_workflow": (
                    settings.comfyui_workflow_flux2_character_lineart_no_reference
                ),
                "flux2_9b_lora_enabled": settings.comfyui_flux2_9b_lora_enabled,
                "flux2_9b_lora_workflow": (
                    settings.comfyui_workflow_flux2_9b_lora
                ),
                "flux2_9b_fast_enabled": settings.comfyui_flux2_9b_fast_enabled,
                "flux2_9b_fast_workflow": (
                    settings.comfyui_workflow_flux2_9b_fast
                ),
                "flux2_9b_fast_lowres_enabled": (
                    settings.comfyui_flux2_9b_fast_lowres_enabled
                ),
                "flux2_9b_fast_lowres_workflow": (
                    settings.comfyui_workflow_flux2_9b_fast_lowres
                ),
                "flux2_4b_source_enabled": settings.comfyui_flux2_4b_source_enabled,
                "flux2_4b_source_workflow": (
                    settings.comfyui_workflow_flux2_4b_source
                ),
                "flux2_4b_color_enabled": settings.comfyui_flux2_4b_color_enabled,
                "flux2_4b_color_workflow": (
                    settings.comfyui_workflow_flux2_4b_color
                ),
                "character_library": character_library,
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
    cache = ResultCache(settings.runtime_dir / "results")
    references = ReferenceImageStore(settings.runtime_dir / "references")
    metadata = _create_metadata(settings)
    identities = WorkIdentityRegistry(settings.work_identity_index)
    processor = ProcessingService(
        cache=cache,
        backend=backend,
        semaphore=asyncio.Semaphore(settings.max_parallel_inference),
        inference_gate=PriorityInferenceGate(settings.max_parallel_inference),
    )
    reference_bank = ReferenceBankService(references, identities)
    pregeneration_store = PregenerationStore(
        settings.runtime_dir / "pregeneration",
        source_cache_max_bytes=settings.source_cache_max_bytes,
    )
    pregeneration = PregenerationService(
        store=pregeneration_store,
        cache=cache,
        processor=processor,
        metadata=metadata,
        reference_bank=reference_bank,
        identities=identities,
        settings=settings,
    )
    return ApplicationContext(
        settings=settings,
        backend=backend,
        cache=cache,
        references=references,
        metadata=metadata,
        identities=identities,
        processor=processor,
        reference_bank=reference_bank,
        pregeneration_store=pregeneration_store,
        pregeneration=pregeneration,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 Comic Enhancer FastAPI 应用。"""
    _configure_application_logging()
    context = _create_context(settings or load_settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """管理应用启动和关闭期间的后台资源。"""
        context.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        log_operation(
            logger,
            logging.INFO,
            feature="Comic Enhancer服务启动",
            parameters={
                "backend": context.settings.backend,
                "runtime_dir": str(context.settings.runtime_dir),
                "comfyui_url": context.settings.comfyui_url,
                "flux2_character_enabled": (
                    context.settings.comfyui_flux2_character_enabled
                ),
                "flux2_character_lineart_enabled": (
                    context.settings.comfyui_flux2_character_lineart_enabled
                ),
                "flux2_9b_lora_enabled": (
                    context.settings.comfyui_flux2_9b_lora_enabled
                ),
                "flux2_9b_fast_enabled": (
                    context.settings.comfyui_flux2_9b_fast_enabled
                ),
                "flux2_9b_fast_lowres_enabled": (
                    context.settings.comfyui_flux2_9b_fast_lowres_enabled
                ),
                "flux2_4b_source_enabled": (
                    context.settings.comfyui_flux2_4b_source_enabled
                ),
                "flux2_4b_color_enabled": (
                    context.settings.comfyui_flux2_4b_color_enabled
                ),
            },
            result={"status": "started"},
        )
        try:
            await context.pregeneration.start()
            yield
        finally:
            await context.pregeneration.stop()
            log_operation(
                logger,
                logging.INFO,
                feature="Comic Enhancer服务关闭",
                parameters={"backend": context.settings.backend},
                result={"status": "stopped"},
            )

    app = FastAPI(title="Comic Enhancer", version=__version__, lifespan=lifespan)
    app.state.context = context
    app.state.settings = context.settings
    app.state.processor = context.processor
    app.state.metadata = context.metadata
    app.state.identities = context.identities
    app.state.references = context.references
    app.state.pregeneration = context.pregeneration
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Content-Encoding",
            "X-Comic-Enhancer-Transport",
        ],
    )
    app.add_middleware(GZipRequestMiddleware)
    app.add_middleware(GZipResponseMiddleware, minimum_size=256, compresslevel=6)
    app.include_router(system_router)
    app.include_router(pages_router)
    app.include_router(pregeneration_router)
    app.include_router(metadata_router)
    app.include_router(results_router)
    return app
