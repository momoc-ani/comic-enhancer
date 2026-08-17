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
    comfyui_flux2_character_enabled: bool = False
    comfyui_workflow_flux2_character: Path | None = None
    comfyui_flux2_character_lineart_enabled: bool = False
    comfyui_workflow_flux2_character_lineart: Path | None = None
    comfyui_flux2_character_native_resolution: bool = False
    comfyui_anima_base_enabled: bool = False
    comfyui_workflow_anima_base: Path | None = None
    comfyui_anima_2_9b_enabled: bool = False
    comfyui_workflow_anima_2_9b: Path | None = None
    qwen_vl_base_url: str = "http://127.0.0.1:8080"
    qwen_vl_api_key: str = ""
    qwen_vl_model_id: str = "qwen3-vl-4b-instruct-q8_0"
    qwen_vl_deployment_revision: str = "q8_0-054721f4-mmproj-f16-256f3a43"
    qwen_vl_timeout_seconds: int = 90
    qwen_vl_max_image_edge: int = 2048
    character_min_confidence: float = 0.75
    character_library_root: Path | None = None
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
        "comfyui_workflow_flux2_character",
        "comfyui_workflow_flux2_character_lineart",
        "comfyui_workflow_anima_base",
        "comfyui_workflow_anima_2_9b",
        "character_library_root",
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
        "COMIC_ENHANCER_COMFYUI_FLUX2_CHARACTER_ENABLED": (
            "comfyui_flux2_character_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_WORKFLOW_FLUX2_CHARACTER": (
            "comfyui_workflow_flux2_character",
            Path,
        ),
        "COMIC_ENHANCER_COMFYUI_FLUX2_CHARACTER_NATIVE_RESOLUTION": (
            "comfyui_flux2_character_native_resolution",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_COMFYUI_FLUX2_CHARACTER_LINEART_ENABLED": (
            "comfyui_flux2_character_lineart_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_WORKFLOW_FLUX2_CHARACTER_LINEART": (
            "comfyui_workflow_flux2_character_lineart",
            Path,
        ),
        "COMIC_ENHANCER_COMFYUI_ANIMA_BASE_ENABLED": (
            "comfyui_anima_base_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_WORKFLOW_ANIMA_BASE": (
            "comfyui_workflow_anima_base",
            Path,
        ),
        "COMIC_ENHANCER_COMFYUI_ANIMA_2_9B_ENABLED": (
            "comfyui_anima_2_9b_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_WORKFLOW_ANIMA_2_9B": (
            "comfyui_workflow_anima_2_9b",
            Path,
        ),
        "COMIC_ENHANCER_QWEN_VL_URL": ("qwen_vl_base_url", str),
        "COMIC_ENHANCER_QWEN_VL_API_KEY": ("qwen_vl_api_key", str),
        "COMIC_ENHANCER_QWEN_VL_MODEL_ID": ("qwen_vl_model_id", str),
        "COMIC_ENHANCER_QWEN_VL_DEPLOYMENT_REVISION": (
            "qwen_vl_deployment_revision",
            str,
        ),
        "COMIC_ENHANCER_QWEN_VL_TIMEOUT": ("qwen_vl_timeout_seconds", int),
        "COMIC_ENHANCER_QWEN_VL_MAX_IMAGE_EDGE": ("qwen_vl_max_image_edge", int),
        "COMIC_ENHANCER_CHARACTER_MIN_CONFIDENCE": (
            "character_min_confidence",
            float,
        ),
        "COMIC_ENHANCER_CHARACTER_LIBRARY_ROOT": ("character_library_root", Path),
        "COMIC_ENHANCER_WORKFLOW_FAST": ("comfyui_workflow_fast", Path),
        "COMIC_ENHANCER_WORKFLOW_QUALITY": ("comfyui_workflow_quality", Path),
    }
    for env_name, (field_name, converter) in env_map.items():
        if env_name in os.environ:
            values[field_name] = converter(os.environ[env_name])

    return Settings(**values)
