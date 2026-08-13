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
    comfyui_timeout_seconds: int = 180
    comfyui_poll_interval_seconds: float = 0.25
    comfyui_output_node: str = "15"
    comfyui_checkpoint: str = "SD1.5/SD1.5_GhostMix_V2.0.safetensors"
    comfyui_controlnet: str = "SD1.5/control_v11p_sd15_lineart_fp16.safetensors"
    comfyui_fast_steps: int = 8
    comfyui_quality_steps: int = 12
    comfyui_fast_megapixels: float = 0.55
    comfyui_quality_megapixels: float = 0.85
    comfyui_fast_denoise: float = 0.52
    comfyui_quality_denoise: float = 0.58
    comfyui_workflow_with_lora: Path = PROJECT_ROOT / "workflows" / "sd15-colorize-lora.json"
    comfyui_workflow_without_lora: Path = PROJECT_ROOT / "workflows" / "sd15-colorize.json"
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
        "comfyui_workflow_with_lora",
        "comfyui_workflow_without_lora",
    ):
        if field_name in values:
            values[field_name] = Path(values[field_name])

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
        "COMIC_ENHANCER_RUNTIME_DIR": ("runtime_dir", Path),
        "COMIC_ENHANCER_COMFYUI_URL": ("comfyui_url", str),
        "COMIC_ENHANCER_COMFYUI_TIMEOUT": ("comfyui_timeout_seconds", int),
        "COMIC_ENHANCER_COMFYUI_OUTPUT_NODE": ("comfyui_output_node", str),
        "COMIC_ENHANCER_COMFYUI_CHECKPOINT": ("comfyui_checkpoint", str),
        "COMIC_ENHANCER_COMFYUI_CONTROLNET": ("comfyui_controlnet", str),
        "COMIC_ENHANCER_COMFYUI_FAST_STEPS": ("comfyui_fast_steps", int),
        "COMIC_ENHANCER_COMFYUI_QUALITY_STEPS": ("comfyui_quality_steps", int),
        "COMIC_ENHANCER_COMFYUI_FAST_MEGAPIXELS": (
            "comfyui_fast_megapixels",
            float,
        ),
        "COMIC_ENHANCER_COMFYUI_QUALITY_MEGAPIXELS": (
            "comfyui_quality_megapixels",
            float,
        ),
        "COMIC_ENHANCER_COMFYUI_FAST_DENOISE": ("comfyui_fast_denoise", float),
        "COMIC_ENHANCER_COMFYUI_QUALITY_DENOISE": (
            "comfyui_quality_denoise",
            float,
        ),
        "COMIC_ENHANCER_WORKFLOW_WITH_LORA": ("comfyui_workflow_with_lora", Path),
        "COMIC_ENHANCER_WORKFLOW_WITHOUT_LORA": (
            "comfyui_workflow_without_lora",
            Path,
        ),
    }
    for env_name, (field_name, converter) in env_map.items():
        if env_name in os.environ:
            values[field_name] = converter(os.environ[env_name])

    return Settings(**values)
