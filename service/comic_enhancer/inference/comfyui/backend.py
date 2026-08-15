from __future__ import annotations

from pathlib import Path

from PIL import Image

from ...domain import ProcessingMode, ProcessOptions, ResolvedAdapter
from ..contracts import (
    AdapterPolicy,
    InferenceAssets,
    InferenceBackend,
    InferenceOutcome,
)
from .image_ops import (
    pad_square,
    protect_source_structure,
    restore_geometry,
)
from .strategies import (
    ComfyUIModeStrategy,
    FastModeStrategy,
    Flux2ModeStrategy,
    Flux2QuantModeStrategy,
    QualityModeStrategy,
)
from .transport import ComfyUITransport, bind_io, comfy_path
from .workflows import WorkflowLoader


class ComfyUIBackend(InferenceBackend):
    """注册并分派相互独立的 ComfyUI 档位策略。"""

    name = "comfyui"
    applies_adapters = True
    supported_base_models = frozenset({"sd15-anime"})
    model_profiles = (
        "sd15-colorize",
        "flux2-klein-4b",
        "flux2-klein-4b-qwen3-fp8",
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
            "supported_base_models": self.supported_base_models,
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
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        return self.mode_strategy(options.mode).cache_revision(
            options,
            resolved,
            assets,
        )

    # 方法说明：返回所选档位独立声明的适配器策略。
    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        return self.mode_strategy(options.mode).adapter_policy()

    # 方法说明：将页面推理请求交给所选档位的独立实现。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        return self.mode_strategy(options.mode).process(
            assets,
            output_path,
            options,
            resolved,
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
