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
from .analysis import ChapterAnalyzerClient, PageAnalysisStore
from .backends import create_backend
from .cache import ResultCache
from .config import Settings, load_settings
from .jobs import ProcessingService
from .gitee import GiteeAdapterStore, GiteeError
from .identities import WorkIdentityRegistry
from .metadata import (
    AniListProvider,
    BangumiProvider,
    JikanMALProvider,
    KitsuProvider,
    MangaUpdatesProvider,
    MetadataAggregator,
    ShikimoriProvider,
)
from .models import (
    AdapterManifest,
    Capabilities,
    ChapterAnalysisResult,
    CharacterBankEntry,
    MetadataResolution,
    ProcessingMode,
    ProcessOptions,
    ProcessResult,
    WorkIdentity,
)
from .references import (
    REFERENCE_SELECTION_REVISION,
    ReferenceImageStore,
    assess_reference_image,
    reference_quality_rank,
)
from .workflows import PresetWorkflowLoader

logger = logging.getLogger(__name__)
INTERNAL_ANALYZER_URL = "http://magiv2-analyzer:8770"


def effective_analyzer_profile(profile: str | None) -> str | None:
    if profile is None:
        return None
    return f"{profile}+{REFERENCE_SELECTION_REVISION}"


def prioritized_metadata_candidates(
    resolution: MetadataResolution,
    work: WorkIdentity,
):
    """Prefer an explicitly identified provider before title-search candidates."""
    indexed = list(enumerate(resolution.candidates))
    indexed.sort(
        key=lambda item: (
            -int(
                work.external_ids.get(item[1].provider)
                == item[1].provider_id
            ),
            -item[1].confidence,
            item[0],
        )
    )
    return [candidate for _, candidate in indexed]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    backend_options = {}
    if settings.backend == "comfyui":
        workflow_loader = PresetWorkflowLoader(
            fast_workflow=settings.comfyui_workflow_fast,
            quality_workflow=settings.comfyui_workflow_quality,
            workflow_root=settings.comfyui_workflow_root,
            reference_quality_workflow=settings.comfyui_workflow_reference_quality,
            cobra_workflow=settings.comfyui_workflow_cobra,
            flux2_workflow=settings.comfyui_workflow_flux2,
        )
        backend_options = {
            "base_url": settings.comfyui_url,
            "reference_enabled": settings.comfyui_reference_enabled,
            "reference_ready_file": settings.comfyui_reference_ready_file,
            "cobra_enabled": settings.comfyui_cobra_enabled,
            "cobra_workflow": settings.comfyui_workflow_cobra,
            "cobra_reference_limit": settings.cobra_reference_limit,
            "flux2_enabled": settings.comfyui_flux2_enabled,
            "flux2_workflow": settings.comfyui_workflow_flux2,
            "flux2_reference_limit": settings.flux2_reference_limit,
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
    analyses = PageAnalysisStore(settings.runtime_dir / "analysis")
    analyzer = (
        ChapterAnalyzerClient(
            INTERNAL_ANALYZER_URL,
            timeout_seconds=settings.analyzer_timeout_seconds,
        )
        if settings.analyzer_enabled
        else None
    )
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
    identities = WorkIdentityRegistry(settings.work_identity_index)
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
    app.state.analyzer = analyzer
    app.state.identities = identities
    app.state.references = references
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
        cobra_available = backend.cobra_profile_ready()
        flux2_available = backend.flux2_profile_ready()
        processing_modes = [
            mode for mode in ProcessingMode
            if mode != ProcessingMode.COBRA or cobra_available
            if mode != ProcessingMode.FLUX2 or flux2_available
        ]
        return Capabilities(
            service_version=__version__,
            backend=backend.name,
            ready=backend.ready(),
            adapter_policy=["work", "generic", "none"],
            model_profiles=list(backend.model_profiles),
            processing_modes=processing_modes,
            manganinja_available=bool(
                settings.comfyui_reference_enabled
                and analyzer is not None
                and analyzer.ready()
                and backend.reference_profile_ready()
            ),
            cobra_available=cobra_available,
            flux2_available=flux2_available,
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
            work = identities.enrich(
                WorkIdentity.model_validate(json.loads(work_json))
            )
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
        analysis = None
        character_references: dict[str, bytes] = {}
        if (
            str(options.mode) == "manganinja"
            and settings.comfyui_reference_enabled
            and analyzer is not None
        ):
            analyzer_profile = effective_analyzer_profile(
                await asyncio.to_thread(analyzer.profile)
            )
            analysis = await asyncio.to_thread(
                analyses.get,
                image_bytes,
                work_key=work.key,
                analyzer_profile=analyzer_profile,
            )
            if analyzer_profile is None:
                analysis = None
            if analysis is not None:
                for character in analysis.characters:
                    match = character.match
                    if (
                        match.status != "accepted"
                        or not match.character_id
                        or not match.reference_url
                    ):
                        continue
                    reference_urls = {
                        match.character_id: match.reference_url,
                        f"{match.character_id}:portrait": (
                            match.portrait_reference_url
                        ),
                        f"{match.character_id}:full-body": (
                            match.full_body_reference_url
                        ),
                    }
                    for reference_key, reference_url in reference_urls.items():
                        if reference_key in character_references or not reference_url:
                            continue
                        reference = await asyncio.to_thread(
                            references.get,
                            reference_url,
                        )
                        if reference is not None:
                            character_references[reference_key] = reference
        elif str(options.mode) in {"cobra", "flux2"}:
            resolution = await asyncio.to_thread(metadata.resolve, work)
            reference_limit = (
                settings.cobra_reference_limit
                if str(options.mode) == "cobra"
                else settings.flux2_reference_limit
            )
            for entry, reference in (await _character_bank(resolution, work))[
                :reference_limit
            ]:
                character_references.setdefault(entry.character_id, reference)
        return await processor.process(
            image_bytes,
            None,
            work,
            options,
            analysis=analysis,
            character_references=character_references,
        )

    @app.post("/v1/pages/analyze", response_model=ChapterAnalysisResult)
    async def analyze_pages(
        pages: list[UploadFile] = File(),
        work_json: str = Form(),
        _: None = Depends(authorize),
    ) -> ChapterAnalysisResult:
        if analyzer is None:
            raise HTTPException(status_code=409, detail="人物分析服务未启用")
        if not 1 <= len(pages) <= 8:
            raise HTTPException(status_code=422, detail="一次只能分析 1 到 8 页")
        try:
            work = identities.enrich(
                WorkIdentity.model_validate(json.loads(work_json))
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        page_payloads = []
        for index, page in enumerate(pages):
            image_bytes = await page.read()
            if not image_bytes:
                raise HTTPException(status_code=422, detail="empty image")
            page_payloads.append((page.filename or f"page-{index}.img", image_bytes))

        resolved_metadata = await asyncio.to_thread(metadata.resolve, work)
        character_bank = await _character_bank(resolved_metadata, work)
        result = await asyncio.to_thread(
            analyzer.analyze,
            page_payloads,
            character_bank,
        )
        result.analyzer_profile = effective_analyzer_profile(
            result.analyzer_profile
        ) or result.analyzer_profile
        for page_analysis in result.pages:
            page_analysis.analyzer_profile = result.analyzer_profile
            analyses.put(page_analysis, work_key=work.key)
        return result

    async def _character_bank(
        resolution: MetadataResolution,
        work: WorkIdentity,
    ):
        grouped: dict[str, list[dict[str, object]]] = {}
        seen: set[str] = set()
        for candidate in prioritized_metadata_candidates(resolution, work):
            if candidate.confidence < 0.6:
                continue
            for character in candidate.characters:
                key = f"{character.provider}:{character.provider_id}"
                if key in seen or not character.image_url:
                    continue
                image_bytes = await asyncio.to_thread(
                    references.get,
                    character.image_url,
                )
                if image_bytes is None:
                    continue
                character_id, character_name = identities.canonical_character(
                    work,
                    character,
                )
                quality = assess_reference_image(image_bytes)
                grouped.setdefault(character_id, []).append(
                    {
                        "name": character_name,
                        "image_url": character.image_url,
                        "provider": character.provider,
                        "image_bytes": image_bytes,
                        "quality": quality,
                        "confirmed_source": (
                            work.external_ids.get(candidate.provider)
                            == candidate.provider_id
                        ),
                    }
                )
                seen.add(key)
        entry_groups: list[list[tuple[CharacterBankEntry, bytes]]] = []
        decisions: list[dict[str, object]] = []
        for character_id, candidates in grouped.items():
            eligible = [
                item
                for item in candidates
                if item["quality"].usable and item["quality"].colorful
            ]
            if not eligible:
                logger.info(
                    "角色参考图均不满足彩色质量门槛，跳过: work=%s character=%s",
                    work.key,
                    character_id,
                )
                continue
            best = max(
                eligible,
                key=lambda item: reference_quality_rank(
                    item["quality"],
                    confirmed_source=bool(item["confirmed_source"]),
                    provider=str(item["provider"]),
                ),
            )
            portrait = max(
                (item for item in eligible if not item["quality"].full_body),
                key=lambda item: reference_quality_rank(
                    item["quality"],
                    confirmed_source=bool(item["confirmed_source"]),
                    provider=str(item["provider"]),
                ),
                default=None,
            )
            full_body = max(
                (item for item in eligible if item["quality"].full_body),
                key=lambda item: reference_quality_rank(
                    item["quality"],
                    confirmed_source=bool(item["confirmed_source"]),
                    provider=str(item["provider"]),
                ),
                default=None,
            )
            quality = best["quality"]
            match_views = [item for item in candidates if item["quality"].usable]
            match_views.sort(
                key=lambda item: reference_quality_rank(
                    item["quality"],
                    confirmed_source=bool(item["confirmed_source"]),
                    provider=str(item["provider"]),
                ),
                reverse=True,
            )
            character_entries = []
            for view in match_views:
                character_entries.append(
                    (
                        CharacterBankEntry(
                            character_id=character_id,
                            name=str(best["name"]),
                            image_url=str(best["image_url"]),
                            provider=str(view["provider"]),
                            portrait_reference_url=(
                                str(portrait["image_url"]) if portrait else None
                            ),
                            full_body_reference_url=(
                                str(full_body["image_url"]) if full_body else None
                            ),
                        ),
                        view["image_bytes"],
                    )
                )
            entry_groups.append(character_entries)
            decisions.append(
                {
                    "character_id": character_id,
                    "name": best["name"],
                    "provider": best["provider"],
                    "portrait_provider": portrait["provider"] if portrait else None,
                    "full_body_provider": full_body["provider"] if full_body else None,
                    "size": f"{quality.width}x{quality.height}",
                    "saturation": round(quality.saturation, 1),
                    "alternatives": len(candidates),
                    "match_views": len(match_views),
                }
            )
        logger.info(
            "角色参考图择优完成 work=%s selections=%s",
            work.key,
            json.dumps(decisions[:16], ensure_ascii=False),
        )
        entries = [
            group[view_index]
            for view_index in range(max((len(group) for group in entry_groups), default=0))
            for group in entry_groups
            if view_index < len(group)
        ]
        if len(entries) > 16:
            logger.warning("角色匹配视图超过 16 个，仅保留前 16 个: work=%s", work.key)
            return entries[:16]
        return entries

    @app.post("/v1/metadata/resolve", response_model=MetadataResolution)
    async def resolve_metadata(
        work_json: str = Form(),
        _: None = Depends(authorize),
    ) -> MetadataResolution:
        try:
            work = identities.enrich(
                WorkIdentity.model_validate(json.loads(work_json))
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return await asyncio.to_thread(metadata.resolve, work)

    async def _ensure_remote_adapter(work: WorkIdentity, options: ProcessOptions) -> None:
        required_workflow = (
            "quality"
            if str(options.mode) in {"manganinja", "cobra"}
            else str(options.mode)
        )
        for _, manifest in registry.candidates(
            work,
            prefer_work_adapter=options.prefer_work_adapter,
            allow_generic_adapter=options.allow_generic_adapter,
            compatible_base_models=backend.supported_base_models,
            required_workflow=required_workflow,
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
