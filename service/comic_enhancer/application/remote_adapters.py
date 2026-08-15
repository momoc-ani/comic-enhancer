from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from ..adapters import AdapterRegistry, GiteeAdapterStore
from ..domain import AdapterManifest, ProcessingMode, ProcessOptions, WorkIdentity
from ..inference import InferenceBackend

logger = logging.getLogger(__name__)


@dataclass
class RemoteAdapterService:
    store: GiteeAdapterStore
    registry: AdapterRegistry
    backend: InferenceBackend
    index_path: Path
    weights_root: Path

    async def ensure(self, work: WorkIdentity, options: ProcessOptions) -> None:
        """按需下载当前作品可用的远端适配器。"""
        if options.mode == ProcessingMode.UPSCALE:
            return
        required_workflow = (
            "quality"
            if options.mode in {ProcessingMode.FLUX2, ProcessingMode.FLUX2_QUANT}
            else str(options.mode)
        )
        for _, manifest in self.registry.candidates(
            work,
            prefer_work_adapter=options.prefer_work_adapter,
            allow_generic_adapter=options.allow_generic_adapter,
            compatible_base_models=self.backend.supported_base_models,
            required_workflow=required_workflow,
        ):
            if self.registry.is_available(manifest):
                return
            if not (manifest.download_url or manifest.file):
                continue
            try:
                await self.download(manifest)
            except (OSError, RuntimeError) as error:
                logger.warning("LoRA 自动下载失败 %s: %s", manifest.adapter_id, error)
                continue
            if self.registry.is_available(manifest):
                return

    async def sync(self) -> dict[str, object]:
        """从远端同步适配器索引。"""
        return await asyncio.to_thread(self.store.sync_index, self.index_path)

    async def download(self, manifest: AdapterManifest) -> Path:
        """下载并安装经过校验的适配器。"""
        return await asyncio.to_thread(
            self.store.download_adapter,
            manifest,
            self.weights_root,
        )

    async def publish(self, source: Path, manifest: AdapterManifest) -> AdapterManifest:
        """发布适配器文件并更新远端索引。"""
        return await asyncio.to_thread(
            self.store.publish_adapter,
            source=source,
            manifest=manifest,
            commit_message=f"发布 LoRA {manifest.adapter_id}",
        )
