from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import (
    AdapterManifest,
    AdapterSource,
    ResolvedAdapter,
    WorkIdentity,
)


class AdapterRegistry:
    # 方法说明：初始化当前对象及其运行状态。
    def __init__(
        self,
        index_path: Path,
        generic_adapter_id: str,
        weights_root: Path | None = None,
    ):
        self.index_path = index_path
        self.generic_adapter_id = generic_adapter_id
        self.weights_root = weights_root or index_path.parent

    # 方法说明：读取并解析适配器索引。
    def _read(self) -> dict:
        if not self.index_path.exists():
            return {"schema_version": 1, "generic": None, "works": {}}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    # 方法说明：解析当前作品最终可用的适配器。
    def resolve(
        self,
        work: WorkIdentity,
        *,
        prefer_work_adapter: bool = True,
        allow_generic_adapter: bool = True,
        compatible_base_models: frozenset[str] = frozenset(),
        required_workflow: str | None = None,
    ) -> ResolvedAdapter:
        for source, adapter in self.candidates(
            work,
            prefer_work_adapter=prefer_work_adapter,
            allow_generic_adapter=allow_generic_adapter,
            compatible_base_models=compatible_base_models,
            required_workflow=required_workflow,
        ):
            if self.is_available(adapter):
                return ResolvedAdapter(
                    source=source,
                    adapter=adapter,
                    reason=(
                        "matched work adapter"
                        if source == AdapterSource.WORK
                        else "work adapter unavailable; using generic adapter"
                    ),
                )

        return ResolvedAdapter(
            source=AdapterSource.NONE,
            adapter=None,
            reason="no compatible adapter available",
        )

    # 方法说明：按优先级返回当前作品的适配器候选。
    def candidates(
        self,
        work: WorkIdentity,
        *,
        prefer_work_adapter: bool = True,
        allow_generic_adapter: bool = True,
        compatible_base_models: frozenset[str] = frozenset(),
        required_workflow: str | None = None,
    ) -> list[tuple[AdapterSource, AdapterManifest]]:
        index = self._read()
        candidates: list[tuple[AdapterSource, AdapterManifest]] = []
        if prefer_work_adapter and (work_data := index.get("works", {}).get(work.key)):
            adapter = AdapterManifest.model_validate(work_data)
            if adapter.enabled and self._is_compatible(
                adapter, compatible_base_models, required_workflow
            ):
                candidates.append((AdapterSource.WORK, adapter))
        if allow_generic_adapter and (generic_data := index.get("generic")):
            adapter = AdapterManifest.model_validate(generic_data)
            if (
                adapter.adapter_id == self.generic_adapter_id
                and adapter.enabled
                and self._is_compatible(
                    adapter, compatible_base_models, required_workflow
                )
            ):
                candidates.append((AdapterSource.GENERIC, adapter))
        return candidates

    # 方法说明：检查适配器文件是否完整且可用。
    def is_available(self, adapter: AdapterManifest) -> bool:
        return self._is_available(adapter)

    # 方法说明：检查适配器是否兼容当前基模和工作流。
    @staticmethod
    def _is_compatible(
        adapter: AdapterManifest,
        compatible_base_models: frozenset[str],
        required_workflow: str | None,
    ) -> bool:
        base_model_matches = (
            not compatible_base_models or adapter.base_model in compatible_base_models
        )
        workflow_matches = (
            required_workflow is None or required_workflow in adapter.workflows
        )
        return base_model_matches and workflow_matches

    # 方法说明：检查适配器权重是否存在且校验通过。
    def _is_available(self, adapter: AdapterManifest) -> bool:
        if adapter.file is None:
            return False

        adapter_path = (self.weights_root / adapter.file).resolve()
        try:
            adapter_path.relative_to(self.weights_root.resolve())
        except ValueError:
            return False
        if not adapter_path.is_file():
            return False
        if adapter.sha256:
            return self._sha256(adapter_path) == adapter.sha256.lower()
        return True

    # 方法说明：计算文件的 SHA-256 摘要。
    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
