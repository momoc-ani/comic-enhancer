from __future__ import annotations

from pathlib import Path

from .comfyui import ComfyUIBackend
from .contracts import InferenceBackend
from .passthrough import PassthroughBackend
from .realcugan import RealCuganUpscaler
from .routing import RoutedInferenceBackend


# 方法说明：根据配置创建主推理后端并组合平台原生放大实现。
def create_backend(name: str, **options) -> InferenceBackend:
    upscaler = RealCuganUpscaler(
        enabled=bool(options.pop("realcugan_enabled", False)),
        resource_root=Path(
            options.pop(
                "realcugan_resource_root",
                Path(__file__).resolve().parents[3] / "resource" / "realcugan",
            )
        ),
        timeout_seconds=int(options.pop("realcugan_timeout_seconds", 180)),
    )
    if name == PassthroughBackend.name:
        backend: InferenceBackend = PassthroughBackend()
    elif name == ComfyUIBackend.name:
        backend = ComfyUIBackend(**options)
    else:
        raise ValueError(f"unsupported backend: {name}")
    return RoutedInferenceBackend(backend, upscaler)
