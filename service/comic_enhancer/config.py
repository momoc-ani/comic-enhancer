from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
    admin_token: str = ""
    backend: str = "passthrough"
    generic_adapter_id: str = "generic-anime-v1"
    prefetch_pages: int = 3
    max_parallel_inference: int = 1
    comfyui_url: str = "http://comfyui:8188"
    comfyui_reference_url: str = ""
    comfyui_reference_enabled: bool = False
    comfyui_reference_ready_file: Path | None = None
    comfyui_timeout_seconds: int = 180
    comfyui_poll_interval_seconds: float = 0.25
    comfyui_workflow_fast: Path = PROJECT_ROOT / "workflows" / "sd15-colorize-fast.json"
    comfyui_workflow_quality: Path = PROJECT_ROOT / "workflows" / "sd15-colorize-quality.json"
    comfyui_workflow_reference_quality: Path | None = None
    comfyui_workflow_root: Path = PROJECT_ROOT / "workflows"
    runtime_dir: Path = PROJECT_ROOT / "runtime"
    adapter_index: Path = PROJECT_ROOT / "adapters" / "index.json"
    adapter_weights_root: Path = PROJECT_ROOT / "adapters"
    gitee_enabled: bool = False
    gitee_api_url: str = "https://gitee.com/api/v5"
    gitee_owner: str = ""
    gitee_repo: str = ""
    gitee_branch: str = "main"
    gitee_token: str = ""
    gitee_index_path: str = "adapters/index.json"
    gitee_release_tag: str = "lora"
    gitee_timeout_seconds: int = 60
    metadata_enabled: bool = True
    metadata_ttl_seconds: int = 86400
    metadata_timeout_seconds: int = 8
    mangaupdates_api_url: str = ""
    mangaupdates_api_token: str = ""


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

    for field_name in (
        "adapter_index",
        "adapter_weights_root",
        "runtime_dir",
        "comfyui_workflow_fast",
        "comfyui_workflow_quality",
        "comfyui_workflow_reference_quality",
        "comfyui_reference_ready_file",
        "comfyui_workflow_root",
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
        "COMIC_ENHANCER_ADMIN_TOKEN": ("admin_token", str),
        "COMIC_ENHANCER_BACKEND": ("backend", str),
        "COMIC_ENHANCER_ADAPTER_INDEX": ("adapter_index", Path),
        "COMIC_ENHANCER_ADAPTER_WEIGHTS_ROOT": ("adapter_weights_root", Path),
        "COMIC_ENHANCER_GITEE_ENABLED": ("gitee_enabled", lambda value: value.lower() in {"1", "true", "yes", "on"}),
        "COMIC_ENHANCER_GITEE_API_URL": ("gitee_api_url", str),
        "COMIC_ENHANCER_GITEE_OWNER": ("gitee_owner", str),
        "COMIC_ENHANCER_GITEE_REPO": ("gitee_repo", str),
        "COMIC_ENHANCER_GITEE_BRANCH": ("gitee_branch", str),
        "COMIC_ENHANCER_GITEE_TOKEN": ("gitee_token", str),
        "COMIC_ENHANCER_GITEE_INDEX_PATH": ("gitee_index_path", str),
        "COMIC_ENHANCER_GITEE_RELEASE_TAG": ("gitee_release_tag", str),
        "COMIC_ENHANCER_GITEE_TIMEOUT": ("gitee_timeout_seconds", int),
        "COMIC_ENHANCER_METADATA_ENABLED": ("metadata_enabled", lambda value: value.lower() in {"1", "true", "yes", "on"}),
        "COMIC_ENHANCER_METADATA_TTL": ("metadata_ttl_seconds", int),
        "COMIC_ENHANCER_METADATA_TIMEOUT": ("metadata_timeout_seconds", int),
        "COMIC_ENHANCER_MANGAUPDATES_API_URL": ("mangaupdates_api_url", str),
        "COMIC_ENHANCER_MANGAUPDATES_API_TOKEN": ("mangaupdates_api_token", str),
        "COMIC_ENHANCER_RUNTIME_DIR": ("runtime_dir", Path),
        "COMIC_ENHANCER_COMFYUI_URL": ("comfyui_url", str),
        "COMIC_ENHANCER_COMFYUI_REFERENCE_URL": ("comfyui_reference_url", str),
        "COMIC_ENHANCER_COMFYUI_REFERENCE_ENABLED": (
            "comfyui_reference_enabled",
            lambda value: value.lower() in {"1", "true", "yes", "on"},
        ),
        "COMIC_ENHANCER_COMFYUI_REFERENCE_READY_FILE": (
            "comfyui_reference_ready_file",
            Path,
        ),
        "COMIC_ENHANCER_COMFYUI_TIMEOUT": ("comfyui_timeout_seconds", int),
        "COMIC_ENHANCER_WORKFLOW_FAST": ("comfyui_workflow_fast", Path),
        "COMIC_ENHANCER_WORKFLOW_QUALITY": ("comfyui_workflow_quality", Path),
        "COMIC_ENHANCER_WORKFLOW_REFERENCE_QUALITY": (
            "comfyui_workflow_reference_quality",
            Path,
        ),
        "COMIC_ENHANCER_WORKFLOW_ROOT": ("comfyui_workflow_root", Path),
    }
    for env_name, (field_name, converter) in env_map.items():
        if env_name in os.environ:
            values[field_name] = converter(os.environ[env_name])

    return Settings(**values)
