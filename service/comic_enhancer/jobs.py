from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from .adapters import AdapterRegistry
from .inference import InferenceAssets, InferenceBackend
from .cache import ResultCache
from .models import ProcessOptions, ProcessResult, WorkIdentity


@dataclass
class ProcessingService:
    registry: AdapterRegistry
    cache: ResultCache
    backend: InferenceBackend
    semaphore: asyncio.Semaphore

    # 方法说明：按当前策略处理输入并返回推理结果。
    async def process(
        self,
        image_bytes: bytes,
        reference_bytes: bytes | None,
        work: WorkIdentity,
        options: ProcessOptions,
        character_references: dict[str, bytes] | None = None,
    ) -> ProcessResult:
        assets = InferenceAssets(
            image_bytes=image_bytes,
            reference_bytes=reference_bytes,
            character_references=character_references,
        )
        adapter_policy = self.backend.adapter_policy(assets, options)
        resolved = self.registry.resolve(
            work,
            prefer_work_adapter=(
                options.prefer_work_adapter and adapter_policy.enabled
            ),
            allow_generic_adapter=(
                options.allow_generic_adapter and adapter_policy.enabled
            ),
            compatible_base_models=adapter_policy.compatible_base_models,
            required_workflow=adapter_policy.required_workflow,
        )
        cache_key = self.cache.key(
            image_bytes,
            reference_bytes,
            work,
            options,
            resolved,
            self.backend.cache_revision(options, resolved, assets),
        )
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
                    resolved,
                )

                self.cache.save_metadata(
                    cache_key,
                    {
                        "adapter_applied": outcome.adapter_applied,
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
                resolved,
                output_path,
                started,
                cached=True,
                adapter_applied=bool(metadata.get("adapter_applied", False)),
                reference_applied=bool(metadata.get("reference_applied", False)),
                processed_panels=int(metadata.get("processed_panels", 0)),
                model_profile=str(metadata.get("model_profile", "")),
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
            reference_applied=outcome.reference_applied,
            processed_panels=outcome.processed_panels,
            model_profile=outcome.model_profile,
        )

    # 方法说明：组装统一的页面处理结果。
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
        reference_applied=False,
        processed_panels=0,
        model_profile="",
    ) -> ProcessResult:
        return ProcessResult(
            job_id=uuid.uuid4().hex,
            cache_key=cache_key,
            work_key=work.key,
            mode=options.mode,
            adapter_source=resolved.source,
            adapter_id=resolved.adapter.adapter_id if resolved.adapter else None,
            adapter_applied=adapter_applied,
            reference_applied=reference_applied,
            processed_panels=processed_panels,
            model_profile=model_profile,
            result_url=f"/v1/results/{output_path.name}",
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            cached=cached,
        )
