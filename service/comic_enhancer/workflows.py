from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .models import ProcessOptions, ResolvedAdapter


@dataclass(frozen=True)
class LoadedWorkflow:
    prompt: dict
    source: Path
    adapter_applied: bool


class WorkflowLoader(ABC):
    @abstractmethod
    def load(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> LoadedWorkflow:
        raise NotImplementedError

    @abstractmethod
    def revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> str:
        raise NotImplementedError


class PresetWorkflowLoader(WorkflowLoader):
    """Loads complete API-format workflows without changing model parameters."""

    def __init__(
        self,
        *,
        fast_workflow: Path,
        quality_workflow: Path,
        workflow_root: Path,
    ):
        self.fast_workflow = fast_workflow.resolve()
        self.quality_workflow = quality_workflow.resolve()
        self.workflow_root = workflow_root.resolve()

    def load(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> LoadedWorkflow:
        path, adapter_applied = self._select(options, resolved)

        if not path.is_file():
            raise RuntimeError(f"ComfyUI workflow not found: {path}")
        try:
            prompt = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"ComfyUI workflow is not valid JSON: {path}") from error
        if not isinstance(prompt, dict):
            raise RuntimeError(f"ComfyUI workflow must be an API-format object: {path}")

        return LoadedWorkflow(
            prompt=deepcopy(prompt),
            source=path,
            adapter_applied=adapter_applied,
        )

    def revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> str:
        path, _ = self._select(options, resolved)
        if not path.is_file():
            return f"missing:{path}"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _select(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> tuple[Path, bool]:
        mode = str(options.mode)
        path = self.quality_workflow if mode == "quality" else self.fast_workflow
        if resolved.adapter is not None:
            adapter_workflow = resolved.adapter.workflows.get(mode)
            if adapter_workflow:
                return self._adapter_workflow_path(adapter_workflow), True
        return path, False

    def _adapter_workflow_path(self, relative_path: str) -> Path:
        path = (self.workflow_root / relative_path).resolve()
        try:
            path.relative_to(self.workflow_root)
        except ValueError as error:
            raise RuntimeError("adapter workflow path escapes workflow root") from error
        return path
