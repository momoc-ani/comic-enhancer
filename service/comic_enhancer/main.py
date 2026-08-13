from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import tempfile
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import __version__
from .adapters import AdapterRegistry
from .backends import create_backend
from .cache import ResultCache
from .config import Settings, load_settings
from .jobs import ProcessingService
from .gitee import GiteeAdapterStore, GiteeError
from .metadata import (
    AniListProvider,
    BangumiProvider,
    JikanMALProvider,
    KitsuProvider,
    MangaUpdatesProvider,
    MetadataAggregator,
    ShikimoriProvider,
)
from .models import AdapterManifest, Capabilities, MetadataResolution, ProcessOptions, ProcessResult, WorkIdentity
from .references import ReferenceImageStore
from .workflows import PresetWorkflowLoader

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    backend_options = {}
    if settings.backend == "comfyui":
        workflow_loader = PresetWorkflowLoader(
            fast_workflow=settings.comfyui_workflow_fast,
            quality_workflow=settings.comfyui_workflow_quality,
            workflow_root=settings.comfyui_workflow_root,
            reference_quality_workflow=settings.comfyui_workflow_reference_quality,
        )
        backend_options = {
            "base_url": settings.comfyui_url,
            "reference_base_url": settings.comfyui_reference_url or None,
            "reference_enabled": settings.comfyui_reference_enabled,
            "reference_ready_file": settings.comfyui_reference_ready_file,
            "timeout_seconds": settings.comfyui_timeout_seconds,
            "poll_interval_seconds": settings.comfyui_poll_interval_seconds,
            "workflow_loader": workflow_loader,
        }
    backend = create_backend(settings.backend, **backend_options)
    registry = AdapterRegistry(
        settings.adapter_index,
        settings.generic_adapter_id,
        settings.adapter_weights_root,
    )
    cache = ResultCache(settings.runtime_dir / "results")
    references = ReferenceImageStore(settings.runtime_dir / "references")
    metadata = MetadataAggregator(
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
    metadata_refresh_tasks: dict[str, asyncio.Task] = {}
    processor = ProcessingService(
        registry=registry,
        cache=cache,
        backend=backend,
        semaphore=asyncio.Semaphore(settings.max_parallel_inference),
    )
    gitee_store = None
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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        if gitee_store is not None:
            try:
                await asyncio.to_thread(gitee_store.sync_index, settings.adapter_index)
            except Exception as error:  # startup remains usable with cached index
                logger.warning("Gitee 索引同步失败，将继续使用本地索引: %s", error)
        yield

    app = FastAPI(title="Comic Enhancer", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.processor = processor
    app.state.gitee_store = gitee_store
    app.state.metadata = metadata
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {settings.api_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid API token")

    def authorize_admin(authorization: str | None = Header(default=None)) -> None:
        if not settings.admin_token or authorization != f"Bearer {settings.admin_token}":
            raise HTTPException(status_code=403, detail="admin token required")

    @app.get("/v1/health")
    async def health() -> dict[str, object]:
        return {
            "ready": backend.ready(),
            "version": __version__,
            "backend": backend.name,
        }

    @app.get("/v1/capabilities", response_model=Capabilities)
    async def capabilities(_: None = Depends(authorize)) -> Capabilities:
        return Capabilities(
            service_version=__version__,
            backend=backend.name,
            ready=backend.ready(),
            adapter_policy=["work", "generic", "none"],
            model_profiles=list(backend.model_profiles),
            prefetch_pages=settings.prefetch_pages,
            max_parallel_inference=settings.max_parallel_inference,
        )

    @app.post("/v1/pages/process", response_model=ProcessResult)
    async def process_page(
        image: UploadFile = File(),
        work_json: str = Form(),
        options_json: str = Form(default="{}"),
        _: None = Depends(authorize),
    ) -> ProcessResult:
        try:
            work = WorkIdentity.model_validate(json.loads(work_json))
            options = ProcessOptions.model_validate(json.loads(options_json))
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=422, detail="empty image")
        if len(image_bytes) > 30 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="image exceeds 30 MiB")
        if gitee_store is not None:
            await _ensure_remote_adapter(work, options)
        reference_bytes = None
        if str(options.mode) == "quality" and settings.comfyui_reference_enabled:
            reference_url = work.cover_url
            if reference_url:
                reference_bytes = await asyncio.to_thread(references.get, reference_url)
            if reference_bytes is None and settings.metadata_enabled:
                resolved_metadata = await asyncio.to_thread(metadata.cached, work)
                if resolved_metadata is None:
                    # 第三方站点不能阻塞首张图；结果写入缓存后由后续页面使用。
                    task = metadata_refresh_tasks.get(work.key)
                    if task is None or task.done():
                        task = asyncio.create_task(asyncio.to_thread(metadata.resolve, work))
                        metadata_refresh_tasks[work.key] = task
                        task.add_done_callback(
                            lambda completed, key=work.key: metadata_refresh_tasks.pop(
                                key, None
                            )
                        )
                elif resolved_metadata.selected and resolved_metadata.selected.confidence >= 0.6:
                    reference_url = resolved_metadata.selected.cover_url
                    reference_bytes = await asyncio.to_thread(references.get, reference_url)
        return await processor.process(image_bytes, reference_bytes, work, options)

    @app.post("/v1/metadata/resolve", response_model=MetadataResolution)
    async def resolve_metadata(
        work_json: str = Form(),
        _: None = Depends(authorize),
    ) -> MetadataResolution:
        try:
            work = WorkIdentity.model_validate(json.loads(work_json))
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return await asyncio.to_thread(metadata.resolve, work)

    async def _ensure_remote_adapter(work: WorkIdentity, options: ProcessOptions) -> None:
        for _, manifest in registry.candidates(
            work,
            prefer_work_adapter=options.prefer_work_adapter,
            allow_generic_adapter=options.allow_generic_adapter,
            compatible_base_models=backend.supported_base_models,
            required_workflow=str(options.mode),
        ):
            if registry.is_available(manifest):
                return
            if manifest.download_url or manifest.file:
                try:
                    await asyncio.to_thread(
                        gitee_store.download_adapter,
                        manifest,
                        settings.adapter_weights_root,
                    )
                except (GiteeError, OSError) as error:
                    logger.warning("LoRA 自动下载失败 %s: %s", manifest.adapter_id, error)
                    continue
                if registry.is_available(manifest):
                    return

    @app.post("/v1/adapters/sync")
    async def sync_adapters(_: None = Depends(authorize_admin)) -> dict[str, object]:
        if gitee_store is None:
            raise HTTPException(status_code=409, detail="Gitee 同步未启用")
        try:
            index = await asyncio.to_thread(gitee_store.sync_index, settings.adapter_index)
        except (GiteeError, OSError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "work_count": len(index.get("works", {}))}

    @app.post("/v1/adapters/download")
    async def download_adapter(
        adapter_json: str = Form(),
        _: None = Depends(authorize_admin),
    ) -> dict[str, object]:
        if gitee_store is None:
            raise HTTPException(status_code=409, detail="Gitee 同步未启用")
        try:
            manifest = AdapterManifest.model_validate(json.loads(adapter_json))
            target = await asyncio.to_thread(
                gitee_store.download_adapter, manifest, settings.adapter_weights_root
            )
        except (GiteeError, ValueError, json.JSONDecodeError, OSError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"ok": True, "adapter_id": manifest.adapter_id, "path": str(target)}

    @app.post("/v1/adapters/publish")
    async def publish_adapter(
        adapter_json: str = Form(),
        adapter: UploadFile = File(),
        _: None = Depends(authorize_admin),
    ) -> dict[str, object]:
        if gitee_store is None:
            raise HTTPException(status_code=409, detail="Gitee 发布未启用")
        if not adapter.filename or not adapter.filename.lower().endswith(".safetensors"):
            raise HTTPException(status_code=422, detail="只允许发布 .safetensors LoRA")
        temporary: Path | None = None
        try:
            manifest = AdapterManifest.model_validate(json.loads(adapter_json))
            with tempfile.NamedTemporaryFile(suffix=".safetensors", delete=False) as stream:
                temporary = Path(stream.name)
                while chunk := await adapter.read(1024 * 1024):
                    stream.write(chunk)
            published = await asyncio.to_thread(
                gitee_store.publish_adapter,
                source=temporary,
                manifest=manifest,
                commit_message=f"发布 LoRA {manifest.adapter_id}",
            )
        except (GiteeError, ValueError, json.JSONDecodeError, OSError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return {"ok": True, "adapter": published.model_dump(mode="json")}

    @app.get("/v1/results/{filename}")
    async def result(
        filename: str,
        _: None = Depends(authorize),
    ) -> FileResponse:
        safe_name = Path(filename).name
        matches = list((settings.runtime_dir / "results").glob(f"*/{safe_name}"))
        if not matches:
            raise HTTPException(status_code=404, detail="result not found")
        return FileResponse(matches[0], media_type="image/webp")

    return app


app = create_app()
