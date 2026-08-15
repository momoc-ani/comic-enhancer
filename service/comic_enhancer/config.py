from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


PROJECT_ROOT = Path(
    os.environ.get(
        "COMIC_ENHANCER_PROJECT_ROOT",
        Path(__file__).resolve().parents[2],
    )
).resolve()


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    api_token: str = "comic-enhancer-dev"
    backend: str = "passthrough"
    prefetch_pages: int = 3
    max_parallel_inference: int = 1
    realcugan_enabled: bool = False
    realcugan_resource_root: Path = PROJECT_ROOT / "resource" / "realcugan"
    realcugan_timeout_seconds: int = 180
    comfyui_url: str = "http://comfyui:8188"
    comfyui_timeout_seconds: int = 180
    comfyui_poll_interval_seconds: float = 0.25
    comfyui_flux2_enabled: bool = False
    comfyui_workflow_flux2: Path | None = None
    flux2_reference_limit: int = 3
    comfyui_flux2_quant_enabled: bool = False
    comfyui_workflow_flux2_quant: Path | None = None
    comfyui_workflow_fast: Path = PROJECT_ROOT / "workflows" / "sd15-colorize-fast.json"
    comfyui_workflow_quality: Path = PROJECT_ROOT / "workflows" / "sd15-colorize-quality.json"
    runtime_dir: Path = PROJECT_ROOT / "runtime"
    metadata_enabled: bool = True
    metadata_ttl_seconds: int = 86400
    metadata_timeout_seconds: int = 8
    work_identity_index: Path | None = PROJECT_ROOT / "config" / "work-identities.json"
    mangaupdates_api_url: str = ""
    mangaupdates_api_token: str = ""


# 方法说明：从环境变量和配置文件加载服务设置。
def load_settings() -> Settings:
    config_path = Path(
        os.environ.get(
            "COMIC_ENHANCER_CONFIG",
            PROJECT_ROOT / "config" / "settings.json",
        )
    )
    values: dict[str, object] = {}
    if config_path.exists():
        values.update(json.loads(config_path.read_text(encoding="utf-8")))
    supported_fields = {field.name for field in fields(Settings)}
    values = {key: value for key, value in values.items() if key in supported_fields}

    for field_name in (
        "runtime_dir",
        "realcugan_resource_root",
        "comfyui_workflow_fast",
        "comfyui_workflow_quality",
        "comfyui_workflow_flux2",
        "comfyui_workflow_flux2_quant",
        "work_identity_index",
    ):
        if field_name in values:
            if values[field_name]:
                values[field_name] = Path(values[field_name])
            else:
                values[field_name] = None

    env_map = {
        "COMIC_ENHANCER_HOST": ("host", str),
        "COMIC_ENHANCER_PORT": ("port", int),
        "COMIC_ENHANCER_TOKEN": ("api_token", str),
        "COMIC_ENHANCER_BACKEND": ("backend", str),
        "COMIC_ENHANCER_METADATA_ENABLED": ("metadata_enabled", lambda value: value.lower() in {"1", "true", "yes", "on"}),
        "COMIC_ENHANCER_METADATA_TTL": ("metadata_ttl_seconds", int),
        "COMIC_ENHANCER_METADATA_TIMEOUT": ("metadata_timeout_seconds", int),
        "COMIC_ENHANCER_WORK_IDENTITY_INDEX": ("work_identity_index", Path),
        "COMIC_ENHANCER_MANGAUPDATES_API_URL": ("mangaupdates_api_url", str),
        "COMIC_ENHANCER_MANGAUPDATES_API_TOKEN": ("mangaupdates_api_token", str),
        "COMIC_ENHANCER_RUNTIME_DIR": ("runtime_dir", Path),
        "COMIC_ENHANCER_REALCUGAN_ENABLED": (
            "realcugan_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_REALCUGAN_RESOURCE_ROOT": (
            "realcugan_resource_root",
            Path,
        ),
        "COMIC_ENHANCER_REALCUGAN_TIMEOUT": (
            "realcugan_timeout_seconds",
            int,
        ),
        "COMIC_ENHANCER_COMFYUI_URL": ("comfyui_url", str),
        "COMIC_ENHANCER_COMFYUI_TIMEOUT": ("comfyui_timeout_seconds", int),
        "COMIC_ENHANCER_COMFYUI_FLUX2_ENABLED": (
            "comfyui_flux2_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_WORKFLOW_FLUX2": ("comfyui_workflow_flux2", Path),
        "COMIC_ENHANCER_FLUX2_REFERENCE_LIMIT": ("flux2_reference_limit", int),
        "COMIC_ENHANCER_COMFYUI_FLUX2_QUANT_ENABLED": (
            "comfyui_flux2_quant_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_WORKFLOW_FLUX2_QUANT": (
            "comfyui_workflow_flux2_quant",
            Path,
        ),
        "COMIC_ENHANCER_WORKFLOW_FAST": ("comfyui_workflow_fast", Path),
        "COMIC_ENHANCER_WORKFLOW_QUALITY": ("comfyui_workflow_quality", Path),
    }
    for env_name, (field_name, converter) in env_map.items():
        if env_name in os.environ:
            values[field_name] = converter(os.environ[env_name])

    return Settings(**values)
