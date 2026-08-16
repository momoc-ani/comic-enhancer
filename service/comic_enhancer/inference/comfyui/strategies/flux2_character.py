from __future__ import annotations

from collections import OrderedDict
import hashlib
import logging
from pathlib import Path
import threading
import time
import uuid

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
    "flux2-character-full-chroma-180-source-latent-four-step-v10"
)


logger = logging.getLogger(__name__)


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
        **options,
    ):
        super().__init__(**options)
        self.enabled = enabled
        self.workflow_path = workflow_path
        self.character_library = character_library
        self._contexts: OrderedDict[str, CharacterPromptContext] = OrderedDict()
        self._context_lock = threading.Lock()

    # 方法说明：检查独立工作流、ComfyUI 和角色库是否可用；页面处理不强制依赖在线 VLM。
    def available(self) -> bool:
        workflow_ready = self.workflow_loader.supports_flux2_character()
        comfy_ready = self.transport.profile_ready(
            str(self.mode),
            enabled=self.enabled,
            workflow_supported=workflow_ready,
        )
        library_ready = self.character_library is not None
        sidecar_ready = bool(library_ready and self.character_library.ready())
        available = comfy_ready and library_ready
        log_operation(
            logger,
            logging.INFO if available else logging.WARNING,
            feature="角色提示增强档可用性检查",
            parameters={
                "enabled": self.enabled,
                "workflow_ready": workflow_ready,
            },
            result={
                "available": available,
                "comfyui_ready": comfy_ready,
                "character_library_ready": library_ready,
                "qwen_sidecar_ready": sidecar_ready,
            },
        )
        return available

    # 方法说明：生成包含静态角色档案、提示词、工作流和模型版本的缓存标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> str:
        workflow_revision = self.workflow_loader.revision(options)
        model_revision = (
            self.character_library.model_revision
            if self.character_library is not None
            else "missing-character-library"
        )
        base = (
            f"{workflow_revision}:{FLUX2_CHARACTER_PROCESSING_REVISION}:"
            f"{PROMPT_PLANNER_REVISION}:{model_revision}"
        )
        if assets is None:
            return base
        context = self._prepare(assets)
        return f"{base}:{context.digest}"

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
        if self.workflow_path is None:
            log_operation(
                logger,
                logging.ERROR,
                feature="角色提示增强工作流执行",
                parameters=parameters,
                result={"status": "failed", "stage": "workflow_config"},
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise RuntimeError("角色提示增强工作流未配置")

        context = self._prepare(assets)
        loaded_workflow = self.workflow_loader.load(options)
        guide = build_static_character_guide(
            [
                {
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
        input_images = {"INPUT_IMAGE": assets.image_bytes}
        try:
            generated = self.transport.run(
                loaded_workflow.prompt,
                input_images=input_images,
                output_prefix=f"comic-enhancer/{self.output_prefix}-{uuid.uuid4().hex}",
                prepare_workflow=lambda workflow: _bind_character_guide(
                    workflow,
                    guide,
                ),
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
                    "context_digest": context.digest[:12],
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        generated = restore_geometry(
            assets.image_bytes,
            generated,
            output_scale=FLUX2_OUTPUT_SCALE,
        )
        generated = protect_source_luminance_and_ink(assets.image_bytes, generated)
        save_output(generated, output_path)
        outcome = InferenceOutcome(
            reference_applied=True,
            processed_panels=0,
            model_profile=loaded_workflow.model_profile,
        )
        log_operation(
            logger,
            logging.INFO,
            feature="角色提示增强工作流执行",
            parameters={
                **parameters,
                "context_digest": context.digest[:12],
                "guide_chars": len(guide),
            },
            result={
                "status": "success",
                "profiles": len(context.characters),
                "palette_profiles": len(context.characters),
                "reference_images_uploaded": 0,
                "processed_panels": outcome.processed_panels,
                "model_profile": outcome.model_profile,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return outcome

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
