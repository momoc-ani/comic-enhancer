from __future__ import annotations

from collections import OrderedDict
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
from ..image_ops import protect_source_high_frequency_structure, save_output
from .base import ComfyUIModeStrategy, reference_cache_revision
from .flux2_character import (
    _CharacterWorkflowPlan,
    _append_direct_output_revision,
    _assets_key,
    _bind_character_guide,
)


logger = logging.getLogger(__name__)

FLUX2_CHARACTER_LINEART_PROCESSING_REVISION = (
    "flux2-character-lineart-source-structure-v4"
)
LINEART_CHROMA_GAIN = 1.25
LINEART_CHROMA_BLUR_RADIUS = 1.0
LINEART_LUMINANCE_BLEND = 0.65
LINEART_LUMINANCE_BLUR_RADIUS = 8.0


class Flux2CharacterLineartModeStrategy(ComfyUIModeStrategy):
    """使用角色参考图并只提取色度的线稿保真 FLUX.2 档位。"""

    mode = ProcessingMode.FLUX2_CHARACTER_LINEART
    output_prefix = "flux2-character-lineart"

    # 方法说明：初始化线稿档位及作品级角色上下文缓存。
    def __init__(
        self,
        *,
        enabled: bool,
        workflow_path: Path | None,
        character_library: CharacterLibraryBuilder | None,
        no_reference_workflow_path: Path | None = None,
        **options,
    ):
        super().__init__(**options)
        self.enabled = enabled
        self.workflow_path = workflow_path
        self.no_reference_workflow_path = no_reference_workflow_path
        self.character_library = character_library
        self._contexts: OrderedDict[str, CharacterPromptContext] = OrderedDict()
        self._plans: OrderedDict[str, _CharacterWorkflowPlan] = OrderedDict()
        self._context_lock = threading.Lock()

    # 方法说明：检查参考或无参考线稿工作流与 ComfyUI 是否至少有一条链路可用。
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
            feature="线稿保真档可用性检查",
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

    # 方法说明：按实际执行方案生成线稿策略与工作流缓存版本。
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
                reference_cache_revision(self.workflow_loader, options, assets)
                if self._reference_workflow_ready()
                else "missing:flux2-character-lineart-reference"
            )
            no_reference_revision = (
                self.workflow_loader.flux2_no_reference_revision(lineart=True)
            )
            base = (
                f"{reference_revision}:{no_reference_revision}:"
                f"{FLUX2_CHARACTER_LINEART_PROCESSING_REVISION}:"
                f"{PROMPT_PLANNER_REVISION}:{model_revision}"
            )
        else:
            plan = self._execution_plan(assets)
            if plan.no_reference:
                workflow_revision = (
                    self.workflow_loader.flux2_no_reference_revision(lineart=True)
                )
                base = (
                    f"{workflow_revision}:"
                    f"{FLUX2_CHARACTER_LINEART_PROCESSING_REVISION}:"
                    f"no-reference:{plan.fallback_reason}"
                )
            else:
                workflow_revision = reference_cache_revision(
                    self.workflow_loader,
                    options,
                    assets,
                )
                base = (
                    f"{workflow_revision}:"
                    f"{FLUX2_CHARACTER_LINEART_PROCESSING_REVISION}:"
                    f"{PROMPT_PLANNER_REVISION}:{model_revision}:"
                    f"{plan.context.digest}"
                )
        return _append_direct_output_revision(base, options)

    # 方法说明：执行 0.85MP FLUX.2 并用原图结构恢复线稿页面。
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
            "processing_revision": FLUX2_CHARACTER_LINEART_PROCESSING_REVISION,
            "comfyui_direct_output": options.comfyui_direct_output,
        }
        if not self.available():
            raise RuntimeError("角色线稿保真档未就绪")
        plan = self._execution_plan(assets)
        context = plan.context
        if plan.no_reference:
            loaded_workflow = self.workflow_loader.load_flux2_no_reference(
                lineart=True
            )
        else:
            loaded_workflow = self.workflow_loader.load(options)
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

        # 方法说明：按执行方案绑定角色颜色指南并固定工作流原图尺寸输出。
        def prepare_workflow(workflow: dict) -> None:
            if context is not None:
                _bind_character_guide(workflow, guide)
            _bind_lineart_output(workflow)

        log_operation(
            logger,
            logging.INFO,
            feature=(
                "线稿无参考工作流加载"
                if plan.no_reference
                else "线稿保真工作流加载"
            ),
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "reference_count": len(references),
                "comfyui_direct_output": options.comfyui_direct_output,
                "fallback_reason": plan.fallback_reason,
            },
            result={
                "status": "loaded",
                "context_digest": context.digest[:12] if context else "",
                "input_bytes": len(assets.image_bytes),
            },
        )
        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images=input_images,
            output_prefix=f"comic-enhancer/{self.output_prefix}-{uuid.uuid4().hex}",
            prepare_workflow=prepare_workflow,
        )
        source_size = _source_size(assets.image_bytes)
        if generated.size != source_size:
            raise RuntimeError(
                "线稿保真工作流输出尺寸与原图不一致："
                f"expected={source_size}, actual={generated.size}"
            )

        protected = protect_source_high_frequency_structure(
            assets.image_bytes,
            generated,
            chroma_gain=LINEART_CHROMA_GAIN,
            chroma_blur_radius=LINEART_CHROMA_BLUR_RADIUS,
            luminance_blend=LINEART_LUMINANCE_BLEND,
            luminance_blur_radius=LINEART_LUMINANCE_BLUR_RADIUS,
        )
        save_output(protected, output_path)
        log_operation(
            logger,
            logging.INFO,
            feature="线稿保真结构保护",
            parameters={
                "mode": str(options.mode),
                "source_size": list(source_size),
                "comfyui_direct_output": options.comfyui_direct_output,
            },
            result={
                "status": "success",
                "geometry_handler": "comfyui-workflow",
                "color_source": "flux2-luminance-chroma",
                "structure_source": "source-high-frequency-plus-generated-low-frequency",
                "chroma_gain": LINEART_CHROMA_GAIN,
                "chroma_blur_radius": LINEART_CHROMA_BLUR_RADIUS,
                "luminance_blend": LINEART_LUMINANCE_BLEND,
                "luminance_blur_radius": LINEART_LUMINANCE_BLUR_RADIUS,
                "output_size": list(protected.size),
                "reference_applied": not plan.no_reference,
                "fallback_reason": plan.fallback_reason,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return InferenceOutcome(
            reference_applied=not plan.no_reference,
            processed_panels=0,
            model_profile=loaded_workflow.model_profile,
        )

    # 方法说明：判断线稿参考工作流是否已同时配置并存在。
    def _reference_workflow_ready(self) -> bool:
        return bool(
            self.workflow_path
            and self.workflow_loader.supports_flux2_character_lineart()
        )

    # 方法说明：判断线稿无参考工作流是否已同时配置并存在。
    def _no_reference_workflow_ready(self) -> bool:
        return bool(
            self.no_reference_workflow_path
            and self.workflow_loader.supports_flux2_no_reference(lineart=True)
        )

    # 方法说明：只为同一组角色资源选择一次线稿参考或无参考执行方案。
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
                    feature="线稿角色档案准备",
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
            feature="线稿保真执行方案",
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

    # 方法说明：读取或构建作品级角色提示上下文并保留热点缓存。
    def _prepare(self, assets: InferenceAssets) -> CharacterPromptContext:
        started = time.perf_counter()
        if self.character_library is None:
            raise RuntimeError("角色库未配置")
        if not assets.work_key:
            raise RuntimeError("角色线稿保真档缺少作品身份")
        key = _assets_key(assets, self.character_library.model_revision)
        with self._context_lock:
            cached = self._contexts.get(key)
            if cached is not None:
                self._contexts.move_to_end(key)
                log_operation(
                    logger,
                    logging.INFO,
                    feature="线稿保真静态提示上下文缓存读取",
                    parameters={
                        "work_key": assets.work_key,
                        "assets_digest": key[:12],
                    },
                    result={
                        "cache_hit": True,
                        "context_digest": cached.digest[:12],
                    },
                    elapsed_ms=(time.perf_counter() - started) * 1000,
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
            feature="线稿保真静态提示上下文缓存读取",
            parameters={
                "work_key": assets.work_key,
                "assets_digest": key[:12],
            },
            result={
                "cache_hit": False,
                "context_digest": context.digest[:12],
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return context


# 方法说明：读取原图经过 EXIF 方向校正后的准确宽高。
def _source_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as source_file:
        return ImageOps.exif_transpose(source_file).size


# 方法说明：把线稿工作流输出固定绑定到原图尺寸恢复节点。
def _bind_lineart_output(workflow: dict) -> None:
    source_nodes = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == "LoadImage"
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == "INPUT_IMAGE"
    ]
    size_nodes = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == "GetImageSize"
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == "SOURCE_IMAGE_SIZE"
    ]
    restore_nodes = [
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
    if (
        len(source_nodes) != 1
        or len(size_nodes) != 1
        or len(restore_nodes) != 1
        or len(output_nodes) != 1
    ):
        raise RuntimeError("线稿保真工作流缺少唯一尺寸恢复节点")
    source_id, _ = source_nodes[0]
    size_id, size_node = size_nodes[0]
    restore_id, restore_node = restore_nodes[0]
    _, output_node = output_nodes[0]
    size_node.setdefault("inputs", {})["image"] = [source_id, 0]
    restore_node.setdefault("inputs", {}).update(
        {
            "width": [size_id, 0],
            "height": [size_id, 1],
            "upscale_method": "lanczos",
            "crop": "disabled",
        }
    )
    output_node.setdefault("inputs", {})["images"] = [restore_id, 0]


__all__ = [
    "FLUX2_CHARACTER_LINEART_PROCESSING_REVISION",
    "Flux2CharacterLineartModeStrategy",
]
