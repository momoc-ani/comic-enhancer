from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..character_library import CharacterReferenceAsset
from ..domain import ProcessOptions, ProcessResult, WorkIdentity
from ..inference import InferenceAssets, InferenceBackend
from ..logging_utils import log_operation
from ..storage import ResultCache


logger = logging.getLogger(__name__)


@dataclass
class ProcessingService:
    cache: ResultCache
    backend: InferenceBackend
    semaphore: asyncio.Semaphore

    async def process(
        self,
        image_bytes: bytes,
        reference_bytes: bytes | None,
        work: WorkIdentity,
        options: ProcessOptions,
        character_references: dict[str, bytes] | None = None,
        character_reference_assets: tuple[CharacterReferenceAsset, ...] = (),
    ) -> ProcessResult:
        """按当前策略处理输入并返回推理结果。"""
        started = time.perf_counter()
        assets = InferenceAssets(
            image_bytes=image_bytes,
            work_key=work.key,
            reference_bytes=reference_bytes,
            character_references=character_references,
            character_reference_assets=character_reference_assets,
        )
        try:
            backend_revision = await asyncio.to_thread(
                self.backend.cache_revision,
                options,
                assets,
            )
        except Exception as error:
            log_operation(
                logger,
                logging.ERROR,
                feature="页面处理",
                parameters={
                    "work_key": work.key,
                    "mode": str(options.mode),
                    "page_index": options.page_index,
                },
                result={
                    "status": "failed",
                    "stage": "cache_revision",
                    "error": type(error).__name__,
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        cache_key = self.cache.key(
            image_bytes,
            reference_bytes,
            work,
            options,
            backend_revision,
        )
        output_path = self.cache.result_path(cache_key)

        if output_path.exists():
            metadata = self.cache.load_metadata(cache_key)
            return self._result(
                cache_key,
                work,
                options,
                output_path,
                started,
                cached=True,
                reference_applied=bool(metadata.get("reference_applied", False)),
                processed_panels=int(metadata.get("processed_panels", 0)),
                model_profile=str(metadata.get("model_profile", "")),
                comfyui_direct_output=options.comfyui_direct_output,
            )

        async with self.semaphore:
            outcome = None
            if not output_path.exists():
                try:
                    outcome = await asyncio.to_thread(
                        self.backend.process,
                        assets,
                        output_path,
                        options,
                    )
                except Exception as error:
                    log_operation(
                        logger,
                        logging.ERROR,
                        feature="页面处理",
                        parameters={
                            "work_key": work.key,
                            "mode": str(options.mode),
                            "page_index": options.page_index,
                            "cache_key": cache_key[:12],
                        },
                        result={
                            "status": "failed",
                            "stage": "inference",
                            "error": type(error).__name__,
                        },
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                    )
                    raise
                self.cache.save_metadata(
                    cache_key,
                    {
                        "reference_applied": outcome.reference_applied,
                        "processed_panels": outcome.processed_panels,
                        "model_profile": outcome.model_profile,
                        "comfyui_direct_output": options.comfyui_direct_output,
                    },
                )

        if outcome is None:
            metadata = self.cache.load_metadata(cache_key)
            return self._result(
                cache_key,
                work,
                options,
                output_path,
                started,
                cached=True,
                reference_applied=bool(metadata.get("reference_applied", False)),
                processed_panels=int(metadata.get("processed_panels", 0)),
                model_profile=str(metadata.get("model_profile", "")),
                comfyui_direct_output=options.comfyui_direct_output,
            )

        return self._result(
            cache_key,
            work,
            options,
            output_path,
            started,
            cached=False,
            reference_applied=outcome.reference_applied,
            processed_panels=outcome.processed_panels,
            model_profile=outcome.model_profile,
            comfyui_direct_output=options.comfyui_direct_output,
        )

    def _result(
        self,
        cache_key: str,
        work: WorkIdentity,
        options: ProcessOptions,
        output_path: Path,
        started: float,
        *,
        cached: bool,
        reference_applied: bool = False,
        processed_panels: int = 0,
        model_profile: str = "",
        comfyui_direct_output: bool = False,
    ) -> ProcessResult:
        """组装统一的页面处理结果。"""
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        result = ProcessResult(
            job_id=uuid.uuid4().hex,
            cache_key=cache_key,
            work_key=work.key,
            mode=options.mode,
            reference_applied=reference_applied,
            processed_panels=processed_panels,
            model_profile=model_profile,
            result_url=f"/v1/results/{output_path.name}",
            elapsed_ms=elapsed_ms,
            cached=cached,
            comfyui_direct_output=comfyui_direct_output,
        )
        log_operation(
            logger,
            logging.INFO,
            feature="页面处理",
            parameters={
                "work_key": work.key,
                "mode": str(options.mode),
                "page_index": options.page_index,
                "cache_key": cache_key[:12],
            },
            result={
                "status": "success",
                "cached": cached,
                "reference_applied": reference_applied,
                "processed_panels": processed_panels,
                "model_profile": model_profile,
                "comfyui_direct_output": comfyui_direct_output,
            },
            elapsed_ms=elapsed_ms,
        )
        return result
