from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from pathlib import Path
import time
import uuid

import httpx
from PIL import Image, ImageEnhance, ImageOps

from .models import ProcessOptions, ResolvedAdapter


class InferenceBackend(ABC):
    name: str
    applies_adapters: bool = False
    supported_base_models: frozenset[str] = frozenset()

    def ready(self) -> bool:
        return True

    @abstractmethod
    def process(
        self,
        image_bytes: bytes,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> "InferenceOutcome":
        raise NotImplementedError


@dataclass(frozen=True)
class InferenceOutcome:
    adapter_applied: bool


class PassthroughBackend(InferenceBackend):
    """Development backend preserving the full API without bundling model weights."""

    name = "passthrough"

    def process(
        self,
        image_bytes: bytes,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if options.mode == "quality":
                image = ImageEnhance.Contrast(image).enhance(1.04)
                image = ImageEnhance.Sharpness(image).enhance(1.08)
            image.save(output_path, format="WEBP", quality=92, method=4)
        return InferenceOutcome(adapter_applied=False)


class ComfyUIBackend(InferenceBackend):
    name = "comfyui"
    applies_adapters = True
    supported_base_models = frozenset({"sd15-anime"})

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
        output_node: str,
        checkpoint: str,
        controlnet: str,
        fast_steps: int,
        quality_steps: int,
        fast_megapixels: float,
        quality_megapixels: float,
        fast_denoise: float,
        quality_denoise: float,
        workflow_with_lora: Path,
        workflow_without_lora: Path,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.output_node = output_node
        self.checkpoint = checkpoint
        self.controlnet = controlnet
        self.fast_steps = fast_steps
        self.quality_steps = quality_steps
        self.fast_megapixels = fast_megapixels
        self.quality_megapixels = quality_megapixels
        self.fast_denoise = fast_denoise
        self.quality_denoise = quality_denoise
        self.workflow_with_lora = workflow_with_lora
        self.workflow_without_lora = workflow_without_lora

    def ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/system_stats", timeout=2)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def process(
        self,
        image_bytes: bytes,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        use_adapter = resolved.adapter is not None and resolved.adapter.file is not None
        workflow_path = (
            self.workflow_with_lora if use_adapter else self.workflow_without_lora
        )
        if not workflow_path.is_file():
            raise RuntimeError(f"ComfyUI workflow not found: {workflow_path}")

        upload_name = f"comic-enhancer-{uuid.uuid4().hex}.png"
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            upload = client.post(
                "/upload/image",
                files={"image": (upload_name, image_bytes, "image/png")},
                data={"type": "input", "overwrite": "true"},
            )
            upload.raise_for_status()
            uploaded = upload.json()
            comfy_input = self._comfy_path(uploaded)

            replacements: dict[str, object] = {
                "${INPUT_IMAGE}": comfy_input,
                "${OUTPUT_PREFIX}": f"comic-enhancer/{uuid.uuid4().hex}",
                "${CHECKPOINT_NAME}": self.checkpoint,
                "${CONTROLNET_NAME}": self.controlnet,
                "${LORA_NAME}": resolved.adapter.file if use_adapter else "",
                "${LORA_STRENGTH}": (
                    resolved.adapter.recommended_weight if use_adapter else 0.0
                ),
                "${SEED}": int.from_bytes(
                    hashlib.sha256(image_bytes).digest()[:8], "big"
                ),
                "${STEPS}": (
                    self.quality_steps
                    if options.mode == "quality"
                    else self.fast_steps
                ),
                "${TARGET_MEGAPIXELS}": (
                    self.quality_megapixels
                    if options.mode == "quality"
                    else self.fast_megapixels
                ),
                "${DENOISE}": (
                    self.quality_denoise
                    if options.mode == "quality"
                    else self.fast_denoise
                ),
            }
            workflow = self._load_workflow(workflow_path, replacements)
            client_id = uuid.uuid4().hex
            queued = client.post("/prompt", json={"prompt": workflow, "client_id": client_id})
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            image_info = self._wait_for_output(client, prompt_id)
            result = client.get("/view", params=image_info)
            result.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        with Image.open(BytesIO(result.content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        return InferenceOutcome(adapter_applied=use_adapter)

    def _wait_for_output(self, client: httpx.Client, prompt_id: str) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = client.get(f"/history/{prompt_id}")
            response.raise_for_status()
            history = response.json().get(prompt_id)
            if history:
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI prompt failed: {status}")
                output = history.get("outputs", {}).get(self.output_node, {})
                images = output.get("images", [])
                if images:
                    image = images[-1]
                    return {
                        "filename": image["filename"],
                        "subfolder": image.get("subfolder", ""),
                        "type": image.get("type", "output"),
                    }
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")

    @staticmethod
    def _load_workflow(path: Path, replacements: dict[str, object]) -> dict:
        workflow = json.loads(path.read_text(encoding="utf-8"))

        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return replacements.get(value, value) if isinstance(value, str) else value

        return replace(workflow)

    @staticmethod
    def _comfy_path(uploaded: dict) -> str:
        name = uploaded["name"]
        subfolder = uploaded.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else name


def create_backend(name: str, **options) -> InferenceBackend:
    if name == PassthroughBackend.name:
        return PassthroughBackend()
    if name == ComfyUIBackend.name:
        return ComfyUIBackend(**options)
    raise ValueError(f"unsupported backend: {name}")
