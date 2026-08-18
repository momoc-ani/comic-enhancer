from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...character_library import CharacterLibraryBuilder
from ...domain import ProcessingMode, ProcessOptions
from ..contracts import (
    InferenceAssets,
    InferenceBackend,
    InferenceOutcome,
)
from .image_ops import (
    pad_square,
    protect_source_structure,
    restore_geometry,
)
from .ocr_text_processing import OCRTextProtectionProcessor
from .strategies import (
    ComfyUIModeStrategy,
    FastModeStrategy,
    Flux2ModeStrategy,
    Flux2CharacterModeStrategy,
    Flux2CharacterLineartModeStrategy,
    Flux2QuantModeStrategy,
    Flux29BLoraModeStrategy,
    Flux29BFastModeStrategy,
    Flux29BFastLowresModeStrategy,
    Flux24BSourceModeStrategy,
    Flux24BColorModeStrategy,
    QualityModeStrategy,
)
from .transport import ComfyUITransport, bind_io, comfy_path
from .workflows import WorkflowLoader


class ComfyUIBackend(InferenceBackend):
    """注册并分派相互独立的 ComfyUI 档位策略。"""

    name = "comfyui"
    model_profiles = (
        "sd15-colorize",
        "flux2-klein-4b",
        "flux2-klein-4b-qwen3-fp8",
        "flux2-klein-4b-qwen3-vl-character",
        "flux2-klein-4b-character-no-reference",
        "flux2-klein-4b-qwen3-vl-character-lineart",
        "flux2-klein-4b-character-lineart-no-reference",
        "flux2-klein-9b-lora",
        "flux2-klein-9b-fast",
        "flux2-klein-9b-fast-lowres",
        "flux2-klein-4b-source",
        "flux2-klein-4b-color",
    )

    # 方法说明：初始化传输层并注册每个处理档位的独立策略实现。
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
        workflow_loader: WorkflowLoader,
        flux2_enabled: bool = False,
        flux2_workflow: Path | None = None,
        flux2_reference_limit: int = 3,
        flux2_quant_enabled: bool = False,
        flux2_quant_workflow: Path | None = None,
        flux2_character_enabled: bool = False,
        flux2_character_workflow: Path | None = None,
        flux2_character_no_reference_workflow: Path | None = None,
        flux2_character_native_resolution: bool = False,
        flux2_character_lineart_enabled: bool = False,
        flux2_character_lineart_workflow: Path | None = None,
        flux2_character_lineart_no_reference_workflow: Path | None = None,
        flux2_9b_lora_enabled: bool = False,
        flux2_9b_lora_workflow: Path | None = None,
        flux2_9b_fast_enabled: bool = False,
        flux2_9b_fast_workflow: Path | None = None,
        flux2_9b_fast_lowres_enabled: bool = False,
        flux2_9b_fast_lowres_workflow: Path | None = None,
        flux2_4b_source_enabled: bool = False,
        flux2_4b_source_workflow: Path | None = None,
        flux2_4b_color_enabled: bool = False,
        flux2_4b_color_workflow: Path | None = None,
        character_library: CharacterLibraryBuilder | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.workflow_loader = workflow_loader
        self.transport = ComfyUITransport(
            base_url=self.base_url,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        shared_options = {
            "workflow_loader": workflow_loader,
            "transport": self.transport,
        }
        quality_strategy = QualityModeStrategy(**shared_options)
        strategies: tuple[ComfyUIModeStrategy, ...] = (
            FastModeStrategy(**shared_options),
            quality_strategy,
            Flux2ModeStrategy(
                enabled=flux2_enabled,
                workflow_path=flux2_workflow,
                reference_limit=flux2_reference_limit,
                **shared_options,
            ),
            Flux2QuantModeStrategy(
                enabled=flux2_quant_enabled,
                workflow_path=flux2_quant_workflow,
                reference_limit=flux2_reference_limit,
                **shared_options,
            ),
            Flux2CharacterModeStrategy(
                enabled=flux2_character_enabled,
                workflow_path=flux2_character_workflow,
                no_reference_workflow_path=flux2_character_no_reference_workflow,
                native_resolution=flux2_character_native_resolution,
                character_library=character_library,
                **shared_options,
            ),
            Flux2CharacterLineartModeStrategy(
                enabled=flux2_character_lineart_enabled,
                workflow_path=flux2_character_lineart_workflow,
                no_reference_workflow_path=(
                    flux2_character_lineart_no_reference_workflow
                ),
                character_library=character_library,
                **shared_options,
            ),
            Flux29BLoraModeStrategy(
                enabled=flux2_9b_lora_enabled,
                workflow_path=flux2_9b_lora_workflow,
                reference_limit=flux2_reference_limit,
                **shared_options,
            ),
            Flux29BFastModeStrategy(
                enabled=flux2_9b_fast_enabled,
                workflow_path=flux2_9b_fast_workflow,
                reference_limit=flux2_reference_limit,
                image_processor=OCRTextProtectionProcessor(),
                **shared_options,
            ),
            Flux29BFastLowresModeStrategy(
                enabled=flux2_9b_fast_lowres_enabled,
                workflow_path=flux2_9b_fast_lowres_workflow,
                reference_limit=flux2_reference_limit,
                **shared_options,
            ),
            Flux24BSourceModeStrategy(
                enabled=flux2_4b_source_enabled,
                workflow_path=flux2_4b_source_workflow,
                reference_limit=flux2_reference_limit,
                **shared_options,
            ),
            Flux24BColorModeStrategy(
                enabled=flux2_4b_color_enabled,
                workflow_path=flux2_4b_color_workflow,
                reference_limit=flux2_reference_limit,
                **shared_options,
            ),
        )
        self._mode_strategies = {strategy.mode: strategy for strategy in strategies}

    # 方法说明：检查 ComfyUI 基础服务是否已准备就绪。
    def ready(self) -> bool:
        return self.transport.ready()

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2)

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_QUANT)

    # 方法说明：检查 Qwen3-VL 角色稳定档是否可用。
    def flux2_character_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_CHARACTER)

    # 方法说明：检查角色线稿保真档是否可用。
    def flux2_character_lineart_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_CHARACTER_LINEART)

    # 方法说明：检查 9B LoRA 画质档是否可用。
    def flux2_9b_lora_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_9B_LORA)

    # 方法说明：检查 9B FP8 快速计算档是否可用。
    def flux2_9b_fast_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_9B_FAST)

    # 方法说明：检查 9B FP8 低分辨率快速档是否可用。
    def flux2_9b_fast_lowres_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_9B_FAST_LOWRES)

    # 方法说明：检查 4B source latent 结构稳定档是否可用。
    def flux2_4b_source_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_4B_SOURCE)

    # 方法说明：检查 4B 色彩增强档是否可用。
    def flux2_4b_color_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_4B_COLOR)

    # 方法说明：检查指定处理档位是否可用。
    def mode_available(self, mode: ProcessingMode | str) -> bool:
        return self.mode_strategy(mode).available()

    # 方法说明：返回指定处理档位的独立策略实现。
    def mode_strategy(
        self,
        mode: ProcessingMode | str,
    ) -> ComfyUIModeStrategy:
        normalized = ProcessingMode(mode)
        try:
            return self._mode_strategies[normalized]
        except KeyError as error:
            raise ValueError(f"unsupported processing mode: {normalized}") from error

    # 方法说明：兼容旧调用并返回指定处理档位策略。
    def _strategy(self, mode: ProcessingMode | str) -> ComfyUIModeStrategy:
        return self.mode_strategy(mode)

    # 方法说明：生成所选档位影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None = None,
    ) -> str:
        return self.mode_strategy(options.mode).cache_revision(options, assets)

    # 方法说明：将页面推理请求交给所选档位的独立实现。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        return self.mode_strategy(options.mode).process(
            assets,
            output_path,
            options,
        )

    # 方法说明：兼容旧调用并绑定 ComfyUI 工作流输入输出节点。
    @staticmethod
    def _bind_io(
        workflow: dict,
        *,
        input_images: dict[str, str],
        output_prefix: str,
    ) -> tuple[str, ...]:
        return bind_io(
            workflow,
            input_images=input_images,
            output_prefix=output_prefix,
        )

    # 方法说明：兼容旧调用并拼接 ComfyUI 上传文件路径。
    @staticmethod
    def _comfy_path(uploaded: dict) -> str:
        return comfy_path(uploaded)

    # 方法说明：兼容旧调用并将图像填充为正方形。
    @staticmethod
    def _pad_square(image_bytes: bytes, size: int = 512) -> bytes:
        return pad_square(image_bytes, size)

    # 方法说明：兼容旧调用并恢复生成图的原始宽高比例。
    @staticmethod
    def _restore_geometry(
        source_bytes: bytes,
        generated: Image.Image,
        output_scale: int = 1,
    ) -> Image.Image:
        return restore_geometry(source_bytes, generated, output_scale)

    # 方法说明：兼容旧调用并应用预设工作流结构保护。
    @staticmethod
    def _protect_source_structure(
        source_bytes: bytes,
        generated: Image.Image,
    ) -> Image.Image:
        return protect_source_structure(source_bytes, generated)
