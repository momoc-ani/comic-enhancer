from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from ...domain import ProcessOptions


@dataclass(frozen=True)
class LoadedWorkflow:
    """保存已解析的完整 ComfyUI API 工作流。"""

    prompt: dict
    source: Path
    model_profile: str = "sd15-colorize"


class WorkflowLoader(ABC):
    """定义完整 ComfyUI 工作流的加载与版本契约。"""

    # 方法说明：判断工作流加载器是否支持 FLUX.2 档位。
    def supports_flux2(self) -> bool:
        return False

    # 方法说明：判断工作流加载器是否支持 FLUX.2 量化档位。
    def supports_flux2_quant(self) -> bool:
        return False

    # 方法说明：判断工作流加载器是否支持 Qwen3-VL 角色稳定档。
    def supports_flux2_character(self) -> bool:
        return False

    # 方法说明：判断工作流加载器是否支持角色线稿保真档。
    def supports_flux2_character_lineart(self) -> bool:
        return False

    # 方法说明：判断工作流加载器是否支持 9B LoRA 画质档。
    def supports_flux2_9b_lora(self) -> bool:
        return False

    # 方法说明：判断工作流加载器是否支持 4B source latent 档。
    def supports_flux2_4b_source(self) -> bool:
        return False

    # 方法说明：加载指定档位对应的完整工作流。
    @abstractmethod
    def load(self, options: ProcessOptions) -> LoadedWorkflow:
        raise NotImplementedError

    # 方法说明：计算工作流文件的稳定版本标识。
    @abstractmethod
    def revision(self, options: ProcessOptions) -> str:
        raise NotImplementedError


class PresetWorkflowLoader(WorkflowLoader):
    """加载完整 API 格式工作流且不覆盖其中的模型参数。"""

    # 方法说明：初始化各处理档位的工作流路径。
    def __init__(
        self,
        *,
        fast_workflow: Path,
        quality_workflow: Path,
        flux2_workflow: Path | None = None,
        flux2_quant_workflow: Path | None = None,
        flux2_character_workflow: Path | None = None,
        flux2_character_lineart_workflow: Path | None = None,
        flux2_9b_lora_workflow: Path | None = None,
        flux2_4b_source_workflow: Path | None = None,
    ):
        self.fast_workflow = fast_workflow.resolve()
        self.quality_workflow = quality_workflow.resolve()
        self.flux2_workflow = (
            flux2_workflow.resolve() if flux2_workflow is not None else None
        )
        self.flux2_quant_workflow = (
            flux2_quant_workflow.resolve()
            if flux2_quant_workflow is not None
            else None
        )
        self.flux2_character_workflow = (
            flux2_character_workflow.resolve()
            if flux2_character_workflow is not None
            else None
        )
        self.flux2_character_lineart_workflow = (
            flux2_character_lineart_workflow.resolve()
            if flux2_character_lineart_workflow is not None
            else None
        )
        self.flux2_9b_lora_workflow = (
            flux2_9b_lora_workflow.resolve()
            if flux2_9b_lora_workflow is not None
            else None
        )
        self.flux2_4b_source_workflow = (
            flux2_4b_source_workflow.resolve()
            if flux2_4b_source_workflow is not None
            else None
        )

    # 方法说明：判断工作流加载器是否支持 FLUX.2 档位。
    def supports_flux2(self) -> bool:
        return bool(self.flux2_workflow and self.flux2_workflow.is_file())

    # 方法说明：判断工作流加载器是否支持 FLUX.2 量化档位。
    def supports_flux2_quant(self) -> bool:
        return bool(
            self.flux2_quant_workflow and self.flux2_quant_workflow.is_file()
        )

    # 方法说明：判断角色稳定档完整工作流文件是否存在。
    def supports_flux2_character(self) -> bool:
        return bool(
            self.flux2_character_workflow
            and self.flux2_character_workflow.is_file()
        )

    # 方法说明：判断角色线稿保真工作流文件是否存在。
    def supports_flux2_character_lineart(self) -> bool:
        return bool(
            self.flux2_character_lineart_workflow
            and self.flux2_character_lineart_workflow.is_file()
        )

    # 方法说明：判断 9B LoRA 画质档完整工作流文件是否存在。
    def supports_flux2_9b_lora(self) -> bool:
        return bool(
            self.flux2_9b_lora_workflow
            and self.flux2_9b_lora_workflow.is_file()
        )

    # 方法说明：判断 4B source latent 档完整工作流文件是否存在。
    def supports_flux2_4b_source(self) -> bool:
        return bool(
            self.flux2_4b_source_workflow
            and self.flux2_4b_source_workflow.is_file()
        )

    # 方法说明：加载指定处理档位对应的完整工作流。
    def load(self, options: ProcessOptions) -> LoadedWorkflow:
        path, model_profile = self._select(options)
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
            model_profile=model_profile,
        )

    # 方法说明：计算工作流文件的稳定版本标识。
    def revision(self, options: ProcessOptions) -> str:
        path, _ = self._select(options)
        if not path.is_file():
            return f"missing:{path}"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # 方法说明：选择指定档位对应的预设工作流与模型标识。
    def _select(self, options: ProcessOptions) -> tuple[Path, str]:
        mode = str(options.mode)
        if mode == "flux2":
            if self.flux2_workflow is None:
                raise RuntimeError("FLUX.2 工作流未配置")
            return self.flux2_workflow, "flux2-klein-4b"
        if mode == "flux2_quant":
            if self.flux2_quant_workflow is None:
                raise RuntimeError("FLUX.2 量化工作流未配置")
            return (
                self.flux2_quant_workflow,
                "flux2-klein-4b-qwen3-fp8",
            )
        if mode == "flux2_character":
            if self.flux2_character_workflow is None:
                raise RuntimeError("Qwen3-VL 角色稳定工作流未配置")
            return (
                self.flux2_character_workflow,
                "flux2-klein-4b-qwen3-vl-character",
            )
        if mode == "flux2_character_lineart":
            if self.flux2_character_lineart_workflow is None:
                raise RuntimeError("角色线稿保真工作流未配置")
            return (
                self.flux2_character_lineart_workflow,
                "flux2-klein-4b-qwen3-vl-character-lineart",
            )
        if mode == "flux2_9b_lora":
            if self.flux2_9b_lora_workflow is None:
                raise RuntimeError("FLUX.2 Klein 9B LoRA 工作流未配置")
            return self.flux2_9b_lora_workflow, "flux2-klein-9b-lora"
        if mode == "flux2_4b_source":
            if self.flux2_4b_source_workflow is None:
                raise RuntimeError("FLUX.2 Klein 4B source latent 工作流未配置")
            return self.flux2_4b_source_workflow, "flux2-klein-4b-source"
        path = self.quality_workflow if mode == "quality" else self.fast_workflow
        return path, "sd15-colorize"
