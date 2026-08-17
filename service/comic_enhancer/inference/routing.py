from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
import time

from PIL import Image

from ..domain import ProcessingMode, ProcessOptions
from ..logging_utils import log_operation
from .contracts import (
    InferenceAssets,
    InferenceBackend,
    InferenceOutcome,
)
from .realcugan import RealCuganUpscaler


logger = logging.getLogger(__name__)


class RoutedInferenceBackend(InferenceBackend):
    """在主推理后端之外路由不依赖 ComfyUI 的独立处理档位。"""

    # 方法说明：组合主推理后端与平台原生放大实现。
    def __init__(
        self,
        backend: InferenceBackend,
        upscaler: RealCuganUpscaler,
    ):
        self.backend = backend
        self.upscaler = upscaler
        self.name = backend.name

    # 方法说明：返回当前实际可声明的模型档位。
    @property
    def model_profiles(self) -> tuple[str, ...]:
        profiles = list(self.backend.model_profiles)
        if self.upscale_profile_ready():
            profiles.append(self.upscaler.model_profile)
        return tuple(dict.fromkeys(profiles))

    # 方法说明：检查主推理后端是否已准备就绪。
    def ready(self) -> bool:
        return self.backend.ready()

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return self.backend.flux2_profile_ready() and self.upscale_profile_ready()

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return self.backend.flux2_quant_profile_ready() and self.upscale_profile_ready()

    # 方法说明：检查角色稳定档及其 Real-CUGAN 二阶段是否可用。
    def flux2_character_profile_ready(self) -> bool:
        return self.backend.flux2_character_profile_ready() and self.upscale_profile_ready()

    # 方法说明：检查角色线稿保真档及其 Real-CUGAN 二阶段是否可用。
    def flux2_character_lineart_profile_ready(self) -> bool:
        return (
            self.backend.flux2_character_lineart_profile_ready()
            and self.upscale_profile_ready()
        )

    # 方法说明：检查 9B LoRA 画质档及其 Real-CUGAN 二阶段是否可用。
    def flux2_9b_lora_profile_ready(self) -> bool:
        return (
            self.backend.flux2_9b_lora_profile_ready()
            and self.upscale_profile_ready()
        )

    # 方法说明：检查 9B FP8 快速计算档及其 Real-CUGAN 二阶段是否可用。
    def flux2_9b_fast_profile_ready(self) -> bool:
        return (
            self.backend.flux2_9b_fast_profile_ready()
            and self.upscale_profile_ready()
        )

    # 方法说明：检查 4B source latent 档及其 Real-CUGAN 二阶段是否可用。
    def flux2_4b_source_profile_ready(self) -> bool:
        return (
            self.backend.flux2_4b_source_profile_ready()
            and self.upscale_profile_ready()
        )

    # 方法说明：检查 4B 色彩增强档及其 Real-CUGAN 二阶段是否可用。
    def flux2_4b_color_profile_ready(self) -> bool:
        return (
            self.backend.flux2_4b_color_profile_ready()
            and self.upscale_profile_ready()
        )

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return self.upscaler.available()

    # 方法说明：生成所选档位影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None = None,
    ) -> str:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.cache_revision()
        revision = self.backend.cache_revision(options, assets)
        if options.mode in {
            ProcessingMode.FLUX2,
            ProcessingMode.FLUX2_QUANT,
            ProcessingMode.FLUX2_CHARACTER,
            ProcessingMode.FLUX2_CHARACTER_LINEART,
            ProcessingMode.FLUX2_9B_LORA,
            ProcessingMode.FLUX2_9B_FAST,
            ProcessingMode.FLUX2_4B_SOURCE,
            ProcessingMode.FLUX2_4B_COLOR,
        }:
            return f"{revision}:post-upscale:{self.upscaler.cache_revision()}"
        return revision if self.name == self.backend.name else f"{self.name}:{revision}"

    # 方法说明：将请求路由到 Real-CUGAN 或主推理后端。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.process(assets, output_path)
        if options.mode in {
            ProcessingMode.FLUX2,
            ProcessingMode.FLUX2_QUANT,
            ProcessingMode.FLUX2_CHARACTER,
            ProcessingMode.FLUX2_CHARACTER_LINEART,
            ProcessingMode.FLUX2_9B_LORA,
            ProcessingMode.FLUX2_9B_FAST,
            ProcessingMode.FLUX2_4B_SOURCE,
            ProcessingMode.FLUX2_4B_COLOR,
        }:
            return self._process_flux2_pipeline(assets, output_path, options)
        return self.backend.process(assets, output_path, options)

    # 方法说明：串联 FLUX.2 首阶段和 Real-CUGAN 二阶段放大策略。
    def _process_flux2_pipeline(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        started = time.perf_counter()
        if not self.upscale_profile_ready():
            raise RuntimeError("FLUX.2 二阶段放大资源未就绪")
        stage_path = output_path.with_name(f"{output_path.stem}.flux2-stage.webp")
        stage = "flux2"
        primary_elapsed_ms = 0
        secondary_elapsed_ms = 0
        try:
            primary_started = time.perf_counter()
            primary = self.backend.process(assets, stage_path, options)
            primary_elapsed_ms = round(
                (time.perf_counter() - primary_started) * 1000
            )
            stage_bytes = stage_path.read_bytes()
            with Image.open(BytesIO(stage_bytes)) as stage_image:
                stage_size = stage_image.size
            log_operation(
                logger,
                logging.INFO,
                feature="FLUX.2首阶段完成",
                parameters={
                    "work_key": assets.work_key,
                    "mode": str(options.mode),
                    "comfyui_direct_output": options.comfyui_direct_output,
                },
                result={
                    "status": "success",
                    "model_profile": primary.model_profile,
                    "stage_size": list(stage_size),
                    "stage_bytes": len(stage_bytes),
                    "elapsed_ms": primary_elapsed_ms,
                },
                elapsed_ms=primary_elapsed_ms,
            )
            stage = "realcugan"
            secondary_started = time.perf_counter()
            secondary = self.upscaler.process(
                InferenceAssets(image_bytes=stage_bytes),
                output_path,
            )
            secondary_elapsed_ms = round(
                (time.perf_counter() - secondary_started) * 1000
            )
            output_size = None
            if output_path.is_file():
                with Image.open(output_path) as output_image:
                    output_size = output_image.size
            log_operation(
                logger,
                logging.INFO,
                feature="FLUX.2二阶段放大完成",
                parameters={
                    "work_key": assets.work_key,
                    "mode": str(options.mode),
                    "comfyui_direct_output": options.comfyui_direct_output,
                },
                result={
                    "status": "success",
                    "model_profile": secondary.model_profile,
                    "output_size": list(output_size) if output_size else None,
                    "elapsed_ms": secondary_elapsed_ms,
                },
                elapsed_ms=secondary_elapsed_ms,
            )
            model_profile = (
                primary.model_profile
                if options.mode == ProcessingMode.FLUX2_CHARACTER
                else f"{primary.model_profile}+{secondary.model_profile}"
            )
            outcome = InferenceOutcome(
                reference_applied=primary.reference_applied,
                processed_panels=primary.processed_panels,
                model_profile=model_profile,
            )
            log_operation(
                logger,
                logging.INFO,
                feature="FLUX.2二阶段处理",
                parameters={
                    "work_key": assets.work_key,
                    "mode": str(options.mode),
                },
                result={
                    "status": "success",
                    "primary_model": primary.model_profile,
                    "secondary_model": secondary.model_profile,
                    "model_profile": outcome.model_profile,
                    "primary_elapsed_ms": primary_elapsed_ms,
                    "secondary_elapsed_ms": secondary_elapsed_ms,
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            return outcome
        except Exception as error:
            log_operation(
                logger,
                logging.ERROR,
                feature="FLUX.2二阶段处理",
                parameters={
                    "work_key": assets.work_key,
                    "mode": str(options.mode),
                },
                result={
                    "status": "failed",
                    "stage": stage,
                    "error": type(error).__name__,
                    "primary_elapsed_ms": primary_elapsed_ms,
                    "secondary_elapsed_ms": secondary_elapsed_ms,
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        finally:
            stage_path.unlink(missing_ok=True)
