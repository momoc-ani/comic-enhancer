from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..character_library import CharacterReferenceAsset
from ..domain import ProcessOptions


class InferenceBackend(ABC):
    """定义页面推理后端必须提供的稳定业务契约。"""

    name: str
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

    # 方法说明：检查 Qwen3-VL 角色稳定档位是否可用。
    def flux2_character_profile_ready(self) -> bool:
        return False

    # 方法说明：检查角色线稿保真档位是否可用。
    def flux2_character_lineart_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 Klein 9B LoRA 画质档是否可用。
    def flux2_9b_lora_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 Klein 9B FP8 快速计算档是否可用。
    def flux2_9b_fast_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 Klein 9B FP8 低分辨率快速档是否可用。
    def flux2_9b_fast_lowres_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 Klein 4B source latent 结构稳定档是否可用。
    def flux2_4b_source_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 Klein 4B 色彩增强档是否可用。
    def flux2_4b_color_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return False

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None = None,
    ) -> str:
        return self.name

    # 方法说明：按当前策略处理输入并返回推理结果。
    @abstractmethod
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
    ) -> InferenceOutcome:
        raise NotImplementedError


@dataclass(frozen=True)
class InferenceOutcome:
    """记录一次推理实际使用的模型与输入能力。"""

    reference_applied: bool = False
    processed_panels: int = 0
    model_profile: str = ""


@dataclass(frozen=True)
class InferenceAssets:
    """汇总页面原图及可选参考图字节。"""

    image_bytes: bytes
    work_key: str = ""
    reference_bytes: bytes | None = None
    character_references: dict[str, bytes] | None = None
    character_reference_assets: tuple[CharacterReferenceAsset, ...] = ()
