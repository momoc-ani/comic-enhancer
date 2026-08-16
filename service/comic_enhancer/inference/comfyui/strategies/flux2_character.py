from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from io import BytesIO
import logging
from pathlib import Path
import threading
import time
import uuid

from PIL import Image, ImageOps

from ....character_library import CharacterLibraryBuilder, CharacterPromptContext
from ....character_vision import (
    PROMPT_PLANNER_REVISION,
    build_static_character_guide,
)
from ....domain import ProcessingMode, ProcessOptions
from ....logging_utils import log_operation
from ...contracts import InferenceAssets, InferenceOutcome
from ..image_ops import protect_source_luminance_and_ink, restore_geometry, save_output
from .base import ComfyUIModeStrategy
from .flux2_base import FLUX2_OUTPUT_SCALE


FLUX2_CHARACTER_PROCESSING_REVISION = (
    "flux2-character-full-chroma-180-empty-latent-three-reference-v12"
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CharacterWorkflowPlan:
    """保存一次请求选定的角色参考或无参考执行方案。"""

    context: CharacterPromptContext | None
    fallback_reason: str = ""

    @property
    def no_reference(self) -> bool:
        """判断当前方案是否使用独立无参考工作流。"""
        return self.context is None


class Flux2CharacterModeStrategy(ComfyUIModeStrategy):
    """使用静态角色颜色档案增强独立 FLUX.2 工作流。"""

    mode = ProcessingMode.FLUX2_CHARACTER
    output_prefix = "flux2-character"

    # 方法说明：初始化独立角色提示档位和作品级角色档案缓存。
    def __init__(
        self,
        *,
        enabled: bool,
        workflow_path: Path | None,
        character_library: CharacterLibraryBuilder | None,
        no_reference_workflow_path: Path | None = None,
        native_resolution: bool = False,
        **options,
    ):
        super().__init__(**options)
        self.enabled = enabled
        self.workflow_path = workflow_path
        self.no_reference_workflow_path = no_reference_workflow_path
        self.native_resolution = native_resolution
        self.character_library = character_library
        self._contexts: OrderedDict[str, CharacterPromptContext] = OrderedDict()
        self._plans: OrderedDict[str, _CharacterWorkflowPlan] = OrderedDict()
        self._context_lock = threading.Lock()

    # 方法说明：检查参考或无参考工作流与 ComfyUI 是否至少有一条链路可用。
    def available(self) -> bool:
        reference_workflow_ready = self._reference_workflow_ready()
        no_reference_workflow_ready = self._no_reference_workflow_ready()
        workflow_ready = reference_workflow_ready or no_reference_workflow_ready
        comfy_ready = self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=workflow_ready,
        )
        library_ready = self.character_library is not None
        available = comfy_ready
        log_operation(
            logger,
            logging.INFO if available else logging.WARNING,
            feature="角色提示增强档可用性检查",
            parameters={
                "enabled": self.enabled,
                "workflow_ready": workflow_ready,
                "reference_workflow_ready": reference_workflow_ready,
                "no_reference_workflow_ready": no_reference_workflow_ready,
            },
            result={
                "available": available,
                "comfyui_ready": comfy_ready,
                "character_library_ready": library_ready,
                "qwen_sidecar_required": False,
            },
        )
        return available

    # 方法说明：按实际执行方案生成与参考状态一致的缓存标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        model_revision = (
            self.character_library.model_revision
            if self.character_library is not None
            else "missing-character-library"
        )
        if assets is None:
            reference_revision = (
                self.workflow_loader.revision(options)
                if self._reference_workflow_ready()
                else "missing:flux2-character-reference"
            )
            no_reference_revision = (
                self.workflow_loader.flux2_no_reference_revision()
            )
            base = (
                f"{reference_revision}:{no_reference_revision}:"
                f"{FLUX2_CHARACTER_PROCESSING_REVISION}:"
                f"{PROMPT_PLANNER_REVISION}:{model_revision}"
            )
        else:
            plan = self._execution_plan(assets)
            if plan.no_reference:
                workflow_revision = (
                    self.workflow_loader.flux2_no_reference_revision()
                )
                base = (
                    f"{workflow_revision}:{FLUX2_CHARACTER_PROCESSING_REVISION}:"
                    f"no-reference:{plan.fallback_reason}"
                )
            else:
                workflow_revision = self.workflow_loader.revision(options)
                base = (
                    f"{workflow_revision}:{FLUX2_CHARACTER_PROCESSING_REVISION}:"
                    f"{PROMPT_PLANNER_REVISION}:{model_revision}:"
                    f"{plan.context.digest}"
                )
        if self.native_resolution:
            base = f"{base}:native-resolution-v1"
        return _append_direct_output_revision(base, options)

    # 方法说明：执行独立角色提示工作流，直出 FLUX.2 结果并交给外层本地放大档。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        started = time.perf_counter()
        parameters = {
            "work_key": assets.work_key,
            "references": len(assets.character_reference_assets),
            "processing_revision": FLUX2_CHARACTER_PROCESSING_REVISION,
            "comfyui_direct_output": options.comfyui_direct_output,
            "native_resolution": self.native_resolution,
        }
        if not self.available():
            log_operation(
                logger,
                logging.ERROR,
                feature="角色提示增强工作流执行",
                parameters=parameters,
                result={"status": "failed", "stage": "availability"},
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise RuntimeError("角色提示增强档未就绪")
        plan = self._execution_plan(assets)
        context = plan.context
        if plan.no_reference:
            loaded_workflow = self.workflow_loader.load_flux2_no_reference()
            workflow_revision = self.workflow_loader.flux2_no_reference_revision()
        else:
            loaded_workflow = self.workflow_loader.load(options)
            workflow_revision = self.workflow_loader.revision(options)
        log_operation(
            logger,
            logging.INFO,
            feature=(
                "角色无参考工作流加载"
                if plan.no_reference
                else "角色稳定工作流加载"
            ),
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "comfyui_direct_output": options.comfyui_direct_output,
                "native_resolution": self.native_resolution,
                "reference_count": 0 if context is None else len(context.characters),
                "fallback_reason": plan.fallback_reason,
            },
            result={
                "status": "loaded",
                "workflow_revision": workflow_revision[:16],
                "context_digest": context.digest[:12] if context else "",
                "input_bytes": len(assets.image_bytes),
            },
        )
        guide = ""
        references: list[bytes] = []
        input_images = {"INPUT_IMAGE": assets.image_bytes}
        if context is not None:
            guide = build_static_character_guide(
                [
                    {
                        "reference_slot": character.slot,
                        "display_name": character.profile.display_name,
                        "stable_traits": character.profile.stable_traits,
                        "outfit_traits": character.profile.outfit_traits,
                        "colors": [
                            item.model_dump(mode="json")
                            for item in character.profile.colors
                        ],
                    }
                    for character in context.characters
                ]
            )
            references = [
                character.reference.image_bytes for character in context.characters
            ]
            input_images.update(
                {
                    f"REFERENCE_IMAGE_{slot}": references[
                        min(slot - 1, len(references) - 1)
                    ]
                    for slot in range(1, 4)
                }
            )
        source_size, source_megapixels = _source_geometry(assets.image_bytes)

        # 方法说明：按执行方案绑定角色指南，并按开关调整原图分辨率。
        def prepare_workflow(workflow: dict) -> None:
            if context is not None:
                _bind_character_guide(workflow, guide)
            if self.native_resolution:
                _bind_native_resolution(workflow, source_megapixels)

        try:
            generated = self.transport.run(
                loaded_workflow.prompt,
                input_images=input_images,
                output_prefix=f"comic-enhancer/{self.output_prefix}-{uuid.uuid4().hex}",
                prepare_workflow=prepare_workflow,
            )
        except Exception as error:
            log_operation(
                logger,
                logging.ERROR,
                feature="角色提示增强工作流执行",
                parameters=parameters,
                result={
                    "status": "failed",
                    "stage": "comfyui",
                    "error": type(error).__name__,
                    "context_digest": context.digest[:12] if context else "",
                    "fallback_reason": plan.fallback_reason,
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        comfyui_size = generated.size
        log_operation(
            logger,
            logging.INFO,
            feature="角色稳定ComfyUI输出",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "context_digest": context.digest[:12] if context else "",
                "fallback_reason": plan.fallback_reason,
                "native_resolution": self.native_resolution,
            },
            result={
                "status": "success",
                "size": list(comfyui_size),
                "comfyui_direct_output": options.comfyui_direct_output,
                "source_size": list(source_size),
                "source_megapixels": source_megapixels,
            },
        )
        if self.native_resolution:
            if not options.comfyui_direct_output:
                generated = protect_source_luminance_and_ink(
                    assets.image_bytes,
                    generated,
                )
            log_operation(
                logger,
                logging.INFO,
                feature="角色稳定服务端二次处理",
                parameters={
                    "mode": str(options.mode),
                    "comfyui_direct_output": options.comfyui_direct_output,
                    "native_resolution": True,
                },
                result={
                    "status": "skipped" if options.comfyui_direct_output else "success",
                    "reason": (
                        "comfyui_direct_output"
                        if options.comfyui_direct_output
                        else "structure_protection_only"
                    ),
                    "comfyui_size": list(comfyui_size),
                    "output_size": list(generated.size),
                    "geometry_restore": False,
                    "structure_protection": not options.comfyui_direct_output,
                    "realcugan_stage": "enabled_by_outer_pipeline",
                },
            )
        elif not options.comfyui_direct_output:
            generated = restore_geometry(
                assets.image_bytes,
                generated,
                output_scale=FLUX2_OUTPUT_SCALE,
            )
            generated = protect_source_luminance_and_ink(
                assets.image_bytes,
                generated,
            )
            log_operation(
                logger,
                logging.INFO,
                feature="角色稳定服务端二次处理",
                parameters={
                    "mode": str(options.mode),
                    "output_scale": FLUX2_OUTPUT_SCALE,
                    "comfyui_direct_output": False,
                    "native_resolution": False,
                },
                result={
                    "status": "success",
                    "comfyui_size": list(comfyui_size),
                    "output_size": list(generated.size),
                    "structure_protection": True,
                },
            )
        else:
            log_operation(
                logger,
                logging.INFO,
                feature="角色稳定服务端二次处理",
                parameters={
                    "mode": str(options.mode),
                    "comfyui_direct_output": True,
                    "native_resolution": False,
                },
                result={
                    "status": "skipped",
                    "reason": "comfyui_direct_output",
                    "output_size": list(generated.size),
                    "realcugan_stage": "enabled_by_outer_pipeline",
                },
            )
        save_output(generated, output_path)
        outcome = InferenceOutcome(
            reference_applied=not plan.no_reference,
            processed_panels=0,
            model_profile=loaded_workflow.model_profile,
        )
        log_operation(
            logger,
            logging.INFO,
            feature="角色提示增强工作流执行",
            parameters={
                **parameters,
                "context_digest": context.digest[:12] if context else "",
                "guide_chars": len(guide),
                "fallback_reason": plan.fallback_reason,
            },
            result={
                "status": "success",
                "profiles": 0 if context is None else len(context.characters),
                "palette_profiles": 0 if context is None else len(context.characters),
                "reference_profiles": len(references),
                "reference_images_uploaded": 0 if plan.no_reference else 3,
                "reference_applied": outcome.reference_applied,
                "comfyui_direct_output": options.comfyui_direct_output,
                "native_resolution": self.native_resolution,
                "processed_panels": outcome.processed_panels,
                "model_profile": outcome.model_profile,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return outcome

    # 方法说明：判断角色参考工作流是否已同时配置并存在。
    def _reference_workflow_ready(self) -> bool:
        return bool(
            self.workflow_path
            and self.workflow_loader.supports_flux2_character()
        )

    # 方法说明：判断角色无参考工作流是否已同时配置并存在。
    def _no_reference_workflow_ready(self) -> bool:
        return bool(
            self.no_reference_workflow_path
            and self.workflow_loader.supports_flux2_no_reference()
        )

    # 方法说明：只为同一组角色资源选择一次参考或无参考工作流方案。
    def _execution_plan(self, assets: InferenceAssets) -> _CharacterWorkflowPlan:
        model_revision = (
            self.character_library.model_revision
            if self.character_library is not None
            else "missing-character-library"
        )
        key = _assets_key(assets, model_revision)
        with self._context_lock:
            cached = self._plans.get(key)
            if cached is not None:
                self._plans.move_to_end(key)
                return cached

        if not assets.character_reference_assets:
            plan = _CharacterWorkflowPlan(None, "character_references_unavailable")
        elif not self._reference_workflow_ready():
            plan = _CharacterWorkflowPlan(None, "reference_workflow_unavailable")
        elif self.character_library is None:
            plan = _CharacterWorkflowPlan(None, "character_library_unavailable")
        else:
            try:
                if not self.character_library.ready():
                    plan = _CharacterWorkflowPlan(None, "qwen_sidecar_unavailable")
                else:
                    context = self._prepare(assets)
                    if context.characters:
                        plan = _CharacterWorkflowPlan(context)
                    else:
                        plan = _CharacterWorkflowPlan(None, "qwen_analysis_failed")
            except Exception as error:
                log_operation(
                    logger,
                    logging.WARNING,
                    feature="角色静态档案准备",
                    parameters={"work_key": assets.work_key},
                    result={
                        "status": "fallback",
                        "fallback_reason": "qwen_analysis_failed",
                        "error": type(error).__name__,
                    },
                )
                plan = _CharacterWorkflowPlan(None, "qwen_analysis_failed")

        with self._context_lock:
            self._plans[key] = plan
            self._plans.move_to_end(key)
            while len(self._plans) > 64:
                self._plans.popitem(last=False)
        log_operation(
            logger,
            logging.WARNING if plan.no_reference else logging.INFO,
            feature="角色稳定执行方案",
            parameters={
                "work_key": assets.work_key,
                "references": len(assets.character_reference_assets),
            },
            result={
                "status": "fallback" if plan.no_reference else "reference",
                "fallback_reason": plan.fallback_reason,
                "no_reference_workflow_ready": self._no_reference_workflow_ready(),
            },
        )
        return plan

    # 方法说明：读取或构建作品级静态角色提示上下文并保留热点缓存。
    def _prepare(self, assets: InferenceAssets) -> CharacterPromptContext:
        if self.character_library is None:
            raise RuntimeError("角色库未配置")
        if not assets.work_key:
            raise RuntimeError("角色提示增强档缺少作品身份")
        key = _assets_key(assets, self.character_library.model_revision)
        with self._context_lock:
            cached = self._contexts.get(key)
            if cached is not None:
                self._contexts.move_to_end(key)
                log_operation(
                    logger,
                    logging.INFO,
                    feature="角色静态提示上下文缓存读取",
                    parameters={
                        "work_key": assets.work_key,
                        "assets_digest": key[:12],
                    },
                    result={
                        "cache_hit": True,
                        "context_digest": cached.digest[:12],
                    },
                )
                return cached
        context = self.character_library.prepare_prompt_context(
            work_key=assets.work_key,
            references=assets.character_reference_assets,
        )
        with self._context_lock:
            self._contexts[key] = context
            self._contexts.move_to_end(key)
            while len(self._contexts) > 64:
                self._contexts.popitem(last=False)
        log_operation(
            logger,
            logging.INFO,
            feature="角色静态提示上下文缓存读取",
            parameters={
                "work_key": assets.work_key,
                "assets_digest": key[:12],
            },
            result={
                "cache_hit": False,
                "context_digest": context.digest[:12],
            },
        )
        return context


# 方法说明：按作品和有序角色参考图内容生成静态上下文键，不包含漫画页面。
def _assets_key(assets: InferenceAssets, model_revision: str) -> str:
    digest = hashlib.sha256()
    digest.update(assets.work_key.encode("utf-8"))
    digest.update(model_revision.encode("utf-8"))
    for reference in assets.character_reference_assets:
        digest.update(reference.character_id.encode("utf-8"))
        digest.update(reference.image_bytes)
    return digest.hexdigest()


# 方法说明：为直出和结构保护两种结果生成互不复用的缓存版本。
def _append_direct_output_revision(base: str, options: ProcessOptions) -> str:
    if options.comfyui_direct_output:
        return f"{base}:comfyui-direct-output"
    return base


# 方法说明：读取原图尺寸并计算原图像素量，供原图分辨率工作流使用。
def _source_geometry(image_bytes: bytes) -> tuple[tuple[int, int], float]:
    with Image.open(BytesIO(image_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file)
        width, height = source.size
    return (width, height), round(width * height / 1_000_000, 4)


# 方法说明：把角色工作流的漫画输入和输出切换到原图分辨率路径。
def _bind_native_resolution(workflow: dict, source_megapixels: float) -> None:
    scale_nodes = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == "ImageScaleToTotalPixels"
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == "SCALE MANGA PAGE"
    ]
    if len(scale_nodes) != 1:
        raise RuntimeError("原图分辨率工作流缺少唯一的漫画输入缩放节点")
    scale_nodes[0][1].setdefault("inputs", {})["megapixels"] = source_megapixels

    size_nodes = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == "GetImageSize"
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == "SOURCE_IMAGE_SIZE"
    ]
    output_scale_nodes = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == "ImageScale"
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == "RESTORE SOURCE GEOMETRY"
    ]
    output_nodes = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == "SaveImage"
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == "OUTPUT_IMAGE"
    ]
    if len(size_nodes) != 1 or len(output_scale_nodes) != 1 or len(output_nodes) != 1:
        raise RuntimeError("原图分辨率工作流缺少尺寸校正节点")
    size_id = size_nodes[0][0]
    output_scale_id = output_scale_nodes[0][0]
    output_nodes[0][1].setdefault("inputs", {})["images"] = [output_scale_id, 0]
    output_scale_nodes[0][1].setdefault("inputs", {}).update(
        {
            "width": [size_id, 0],
            "height": [size_id, 1],
        }
    )


# 方法说明：把静态角色颜色指南追加到基础 FLUX.2 正向提示词节点。
def _bind_character_guide(workflow: dict, guide: str) -> None:
    candidates = []
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
            continue
        title = str(node.get("_meta", {}).get("title", "")).strip().upper()
        if title in {"COLORIZATION INSTRUCTION", "GLOBAL_SCENE_PROMPT"}:
            candidates.append(node)
    if len(candidates) != 1:
        raise RuntimeError("角色工作流缺少唯一的基础正向提示词节点")
    inputs = candidates[0].setdefault("inputs", {})
    base_prompt = str(inputs.get("text", "")).strip()
    if not base_prompt:
        raise RuntimeError("角色工作流基础正向提示词为空")
    inputs["text"] = f"{base_prompt}\n\n{guide}"


__all__ = [
    "FLUX2_CHARACTER_PROCESSING_REVISION",
    "Flux2CharacterModeStrategy",
]
