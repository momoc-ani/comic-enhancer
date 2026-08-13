from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from .adapters import AdapterRegistry
from .backends import InferenceBackend
from .cache import ResultCache
from .models import ProcessOptions, ProcessResult, WorkIdentity


@dataclass
class ProcessingService:
    registry: AdapterRegistry
    cache: ResultCache
    backend: InferenceBackend
    semaphore: asyncio.Semaphore

    async def process(
        self,
        image_bytes: bytes,
        work: WorkIdentity,
        options: ProcessOptions,
    ) -> ProcessResult:
        resolved = self.registry.resolve(
            work,
            prefer_work_adapter=options.prefer_work_adapter,
            allow_generic_adapter=options.allow_generic_adapter,
            compatible_base_models=self.backend.supported_base_models,
        )
        cache_key = self.cache.key(image_bytes, work, options, resolved)
        output_path = self.cache.result_path(cache_key)
        started = time.perf_counter()

        if output_path.exists():
            metadata = self.cache.load_metadata(cache_key)
            return self._result(
                cache_key,
                work,
                options,
                resolved,
                output_path,
                started,
                cached=True,
                adapter_applied=bool(metadata.get("adapter_applied", False)),
            )

        async with self.semaphore:
            outcome = None
            if not output_path.exists():
                outcome = await asyncio.to_thread(
                    self.backend.process,
                    image_bytes,
                    output_path,
                    options,
                    resolved,
                )

                self.cache.save_metadata(
                    cache_key,
                    {"adapter_applied": outcome.adapter_applied},
                )

        if outcome is None:
            metadata = self.cache.load_metadata(cache_key)
            return self._result(
                cache_key,
                work,
                options,
                resolved,
                output_path,
                started,
                cached=True,
                adapter_applied=bool(metadata.get("adapter_applied", False)),
            )

        return self._result(
            cache_key,
            work,
            options,
            resolved,
            output_path,
            started,
            cached=False,
            adapter_applied=outcome.adapter_applied,
        )

    def _result(
        self,
        cache_key,
        work,
        options,
        resolved,
        output_path,
        started,
        *,
        cached,
        adapter_applied=False,
    ) -> ProcessResult:
        return ProcessResult(
            job_id=uuid.uuid4().hex,
            cache_key=cache_key,
            work_key=work.key,
            mode=options.mode,
            adapter_source=resolved.source,
            adapter_id=resolved.adapter.adapter_id if resolved.adapter else None,
            adapter_applied=adapter_applied,
            result_url=f"/v1/results/{output_path.name}",
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            cached=cached,
        )
