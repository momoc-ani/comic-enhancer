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
    reference_required: bool = False
    model_profile: str = "sd15-colorize"


class WorkflowLoader(ABC):
    @abstractmethod
    def supports_cobra(self) -> bool:
        raise NotImplementedError

    def supports_flux2(self) -> bool:
        return False

    def supports_flux2_quant(self) -> bool:
        return False

    @abstractmethod
    def load(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        *,
        reference_available: bool = False,
    ) -> LoadedWorkflow:
        raise NotImplementedError

    @abstractmethod
    def revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        *,
        reference_available: bool = False,
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
        cobra_workflow: Path | None = None,
        flux2_workflow: Path | None = None,
        flux2_quant_workflow: Path | None = None,
    ):
        self.fast_workflow = fast_workflow.resolve()
        self.quality_workflow = quality_workflow.resolve()
        self.workflow_root = workflow_root.resolve()
        self.cobra_workflow = (
            cobra_workflow.resolve() if cobra_workflow is not None else None
        )
        self.flux2_workflow = (
            flux2_workflow.resolve() if flux2_workflow is not None else None
        )
        self.flux2_quant_workflow = (
            flux2_quant_workflow.resolve()
            if flux2_quant_workflow is not None
            else None
        )

    def supports_cobra(self) -> bool:
        return bool(self.cobra_workflow and self.cobra_workflow.is_file())

    def supports_flux2(self) -> bool:
        return bool(self.flux2_workflow and self.flux2_workflow.is_file())

    def supports_flux2_quant(self) -> bool:
        return bool(
            self.flux2_quant_workflow and self.flux2_quant_workflow.is_file()
        )

    def load(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        *,
        reference_available: bool = False,
    ) -> LoadedWorkflow:
        path, adapter_applied, reference_required, model_profile = self._select(
            options,
            resolved,
            reference_available=reference_available,
        )

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
            reference_required=reference_required,
            model_profile=model_profile,
        )

    def revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        *,
        reference_available: bool = False,
    ) -> str:
        path, _, _, _ = self._select(
            options,
            resolved,
            reference_available=reference_available,
        )
        if not path.is_file():
            return f"missing:{path}"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _select(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        *,
        reference_available: bool,
    ) -> tuple[Path, bool, bool, str]:
        mode = str(options.mode)
        if mode == "cobra" and self.cobra_workflow is not None:
            return (
                self.cobra_workflow,
                False,
                False,
                "cobra",
            )
        if mode == "flux2" and self.flux2_workflow is not None:
            return (
                self.flux2_workflow,
                False,
                False,
                "flux2-klein-4b",
            )
        if mode == "flux2_quant" and self.flux2_quant_workflow is not None:
            return (
                self.flux2_quant_workflow,
                False,
                False,
                "flux2-klein-4b-qwen3-fp8",
            )
        fallback_mode = (
            "quality" if mode in {"cobra", "flux2", "flux2_quant"} else mode
        )
        path = self.quality_workflow if fallback_mode == "quality" else self.fast_workflow
        if resolved.adapter is not None:
            adapter_workflow = resolved.adapter.workflows.get(fallback_mode)
            if adapter_workflow:
                return (
                    self._adapter_workflow_path(adapter_workflow),
                    True,
                    False,
                    "sd15-colorize-lora",
                )
        return path, False, False, "sd15-colorize"

    def _adapter_workflow_path(self, relative_path: str) -> Path:
        path = (self.workflow_root / relative_path).resolve()
        try:
            path.relative_to(self.workflow_root)
        except ValueError as error:
            raise RuntimeError("adapter workflow path escapes workflow root") from error
        return path
