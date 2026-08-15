from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..domain import ProcessOptions, ResolvedAdapter


class InferenceBackend(ABC):
    """定义页面推理后端必须提供的稳定业务契约。"""

    name: str
    applies_adapters: bool = False
    supported_base_models: frozenset[str] = frozenset()
    model_profiles: tuple[str, ...] = ()

    # 方法说明：检查推理后端是否已准备就绪。
    def ready(self) -> bool:
        return True

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return False

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        return self.name

    # 方法说明：返回当前处理档位的适配器使用策略。
    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=(str(options.mode) if self.applies_adapters else None),
        )

    # 方法说明：按当前策略处理输入并返回推理结果。
    @abstractmethod
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        raise NotImplementedError


@dataclass(frozen=True)
class InferenceOutcome:
    """记录一次推理实际使用的模型与输入能力。"""

    adapter_applied: bool
    reference_applied: bool = False
    processed_panels: int = 0
    model_profile: str = ""


@dataclass(frozen=True)
class InferenceAssets:
    """汇总页面原图及可选参考图字节。"""

    image_bytes: bytes
    reference_bytes: bytes | None = None
    character_references: dict[str, bytes] | None = None


@dataclass(frozen=True)
class AdapterPolicy:
    """描述处理档位允许使用的适配器范围。"""

    enabled: bool
    compatible_base_models: frozenset[str]
    required_workflow: str | None
