from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..domain import ProcessOptions, ProcessResult, WorkIdentity
from ..inference import InferenceAssets, InferenceBackend
from ..storage import ResultCache


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
    ) -> ProcessResult:
        """按当前策略处理输入并返回推理结果。"""
        assets = InferenceAssets(
            image_bytes=image_bytes,
            reference_bytes=reference_bytes,
            character_references=character_references,
        )
        cache_key = self.cache.key(
            image_bytes,
            reference_bytes,
            work,
            options,
            self.backend.cache_revision(options, assets),
        )
        output_path = self.cache.result_path(cache_key)
        started = time.perf_counter()

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
            )

        async with self.semaphore:
            outcome = None
            if not output_path.exists():
                outcome = await asyncio.to_thread(
                    self.backend.process,
                    assets,
                    output_path,
                    options,
                )
                self.cache.save_metadata(
                    cache_key,
                    {
                        "reference_applied": outcome.reference_applied,
                        "processed_panels": outcome.processed_panels,
                        "model_profile": outcome.model_profile,
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
    ) -> ProcessResult:
        """组装统一的页面处理结果。"""
        return ProcessResult(
            job_id=uuid.uuid4().hex,
            cache_key=cache_key,
            work_key=work.key,
            mode=options.mode,
            reference_applied=reference_applied,
            processed_panels=processed_panels,
            model_profile=model_profile,
            result_url=f"/v1/results/{output_path.name}",
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            cached=cached,
        )
