from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import re
import time
import uuid

import httpx
from PIL import Image, ImageEnhance, ImageOps

from .models import ProcessOptions, ResolvedAdapter
from .workflows import WorkflowLoader


class InferenceBackend(ABC):
    name: str
    applies_adapters: bool = False
    supported_base_models: frozenset[str] = frozenset()
    model_profiles: tuple[str, ...] = ()

    def ready(self) -> bool:
        return True

    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: "InferenceAssets | None" = None,
    ) -> str:
        return self.name

    def adapter_policy(
        self,
        assets: "InferenceAssets",
        options: ProcessOptions,
    ) -> "AdapterPolicy":
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=(
                str(options.mode) if self.applies_adapters else None
            ),
        )

    @abstractmethod
    def process(
        self,
        assets: "InferenceAssets",
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> "InferenceOutcome":
        raise NotImplementedError


@dataclass(frozen=True)
class InferenceOutcome:
    adapter_applied: bool
    reference_applied: bool = False
    model_profile: str = ""


@dataclass(frozen=True)
class InferenceAssets:
    image_bytes: bytes
    reference_bytes: bytes | None = None


@dataclass(frozen=True)
class AdapterPolicy:
    enabled: bool
    compatible_base_models: frozenset[str]
    required_workflow: str | None


class PassthroughBackend(InferenceBackend):
    """Development backend preserving the full API without bundling model weights."""

    name = "passthrough"

    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(BytesIO(assets.image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if options.mode == "quality":
                image = ImageEnhance.Contrast(image).enhance(1.04)
                image = ImageEnhance.Sharpness(image).enhance(1.08)
            image.save(output_path, format="WEBP", quality=92, method=4)
        return InferenceOutcome(
            adapter_applied=False,
            model_profile="passthrough",
        )


class ComfyUIBackend(InferenceBackend):
    name = "comfyui"
    applies_adapters = True
    supported_base_models = frozenset({"sd15-anime"})
    model_profiles = ("sd15-colorize", "manganinja-reference")

    def __init__(
        self,
        *,
        base_url: str,
        reference_base_url: str | None,
        timeout_seconds: int,
        poll_interval_seconds: float,
        workflow_loader: WorkflowLoader,
        reference_enabled: bool = False,
        reference_ready_file: Path | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.reference_base_url = (reference_base_url or base_url).rstrip("/")
        self.reference_enabled = reference_enabled
        self.reference_ready_file = reference_ready_file
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.workflow_loader = workflow_loader
        self._reference_ready_cached_until = 0.0
        self._reference_ready_cached_value = False

    def ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/system_stats", timeout=2)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        reference_available = bool(
            assets and assets.reference_bytes and self._reference_ready()
        )
        return self.workflow_loader.revision(
            options,
            resolved,
            reference_available=reference_available,
        )

    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        reference_profile = (
            str(options.mode) == "quality"
            and assets.reference_bytes is not None
            and self.workflow_loader.supports_reference()
            and self._reference_ready()
        )
        return AdapterPolicy(
            enabled=not reference_profile,
            compatible_base_models=(
                self.supported_base_models if not reference_profile else frozenset()
            ),
            required_workflow=(str(options.mode) if not reference_profile else None),
        )

    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        reference_available = bool(
            assets.reference_bytes and self._reference_ready()
        )
        loaded_workflow = self.workflow_loader.load(
            options,
            resolved,
            reference_available=reference_available,
        )

        if loaded_workflow.reference_required and assets.reference_bytes is None:
            raise RuntimeError("reference workflow requires a reference image")
        base_url = (
            self.reference_base_url
            if loaded_workflow.reference_required
            else self.base_url
        )
        page_bytes = assets.image_bytes
        reference_bytes = assets.reference_bytes
        if loaded_workflow.model_profile == "manganinja-reference":
            page_bytes = self._pad_square(assets.image_bytes)
            if reference_bytes is not None:
                reference_bytes = self._pad_square(reference_bytes)
        with httpx.Client(base_url=base_url, timeout=self.timeout_seconds) as client:
            comfy_inputs = {
                "INPUT_IMAGE": self._upload(client, page_bytes, "page"),
            }
            if loaded_workflow.reference_required and reference_bytes is not None:
                comfy_inputs["REFERENCE_IMAGE"] = self._upload(
                    client,
                    reference_bytes,
                    "reference",
                )

            workflow = loaded_workflow.prompt
            output_nodes = self._bind_io(
                workflow,
                input_images=comfy_inputs,
                output_prefix=f"comic-enhancer/{uuid.uuid4().hex}",
            )
            client_id = uuid.uuid4().hex
            queued = client.post("/prompt", json={"prompt": workflow, "client_id": client_id})
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            image_info = self._wait_for_output(client, prompt_id, output_nodes)
            result = client.get("/view", params=image_info)
            result.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        with Image.open(BytesIO(result.content)) as source:
            generated = ImageOps.exif_transpose(source).convert("RGB")
            if loaded_workflow.model_profile == "manganinja-reference":
                generated = self._restore_geometry(
                    assets.image_bytes,
                    generated,
                )
            image = self._protect_source_structure(assets.image_bytes, generated)
            image.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        return InferenceOutcome(
            adapter_applied=loaded_workflow.adapter_applied,
            reference_applied=loaded_workflow.reference_required,
            model_profile=loaded_workflow.model_profile,
        )

    def _reference_ready(self) -> bool:
        if not self.reference_enabled:
            return False
        if self.reference_ready_file is not None and not self.reference_ready_file.is_file():
            return False
        now = time.monotonic()
        if now < self._reference_ready_cached_until:
            return self._reference_ready_cached_value
        try:
            response = httpx.get(
                f"{self.reference_base_url}/system_stats",
                timeout=1,
            )
            ready = response.status_code == 200
        except httpx.HTTPError:
            ready = False
        self._reference_ready_cached_value = ready
        self._reference_ready_cached_until = now + (5 if ready else 1)
        return ready

    def _upload(self, client: httpx.Client, image_bytes: bytes, role: str) -> str:
        upload_name = f"comic-enhancer-{role}-{uuid.uuid4().hex}.png"
        normalized = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source:
            ImageOps.exif_transpose(source).convert("RGB").save(
                normalized,
                format="PNG",
            )
        upload = client.post(
            "/upload/image",
            files={"image": (upload_name, normalized.getvalue(), "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
        upload.raise_for_status()
        return self._comfy_path(upload.json())

    @staticmethod
    def _pad_square(image_bytes: bytes, size: int = 512) -> bytes:
        output = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
            scale = min(size / source.width, size / source.height)
            resized = source.resize(
                (
                    max(1, round(source.width * scale)),
                    max(1, round(source.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        canvas = Image.new("RGB", (size, size), "white")
        canvas.paste(
            resized,
            ((size - resized.width) // 2, (size - resized.height) // 2),
        )
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue()

    @staticmethod
    def _restore_geometry(source_bytes: bytes, generated: Image.Image) -> Image.Image:
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file)
            source_size = source.size
        source_width, source_height = source_size
        if source_width < source_height:
            content_width = max(1, round(generated.width * source_width / source_height))
            left = (generated.width - content_width) // 2
            generated = generated.crop((left, 0, left + content_width, generated.height))
        elif source_height < source_width:
            content_height = max(1, round(generated.height * source_height / source_width))
            top = (generated.height - content_height) // 2
            generated = generated.crop((0, top, generated.width, top + content_height))
        return generated.resize(source_size, Image.Resampling.LANCZOS)

    def _wait_for_output(
        self,
        client: httpx.Client,
        prompt_id: str,
        output_nodes: tuple[str, ...],
    ) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = client.get(f"/history/{prompt_id}")
            response.raise_for_status()
            history = response.json().get(prompt_id)
            if history:
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI prompt failed: {status}")
                outputs = history.get("outputs", {})
                for node_id in reversed(output_nodes):
                    images = outputs.get(node_id, {}).get("images", [])
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
    def _bind_io(
        workflow: dict,
        *,
        input_images: dict[str, str],
        output_prefix: str,
    ) -> tuple[str, ...]:
        load_nodes = [
            (str(node_id), node)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "LoadImage"
        ]
        if len(load_nodes) == 1 and "INPUT_IMAGE" in input_images:
            load_nodes[0][1].setdefault("inputs", {})["image"] = input_images[
                "INPUT_IMAGE"
            ]
        elif len(load_nodes) > 1:
            discovered_roles: set[str] = set()
            for _, node in load_nodes:
                role = str(node.get("_meta", {}).get("title", "")).strip().upper()
                if role in input_images:
                    node.setdefault("inputs", {})["image"] = input_images[role]
                    discovered_roles.add(role)
            missing = sorted(set(input_images) - discovered_roles)
            if missing:
                raise RuntimeError(
                    "ComfyUI workflow is missing titled LoadImage nodes: "
                    + ", ".join(missing)
                )
        else:
            raise RuntimeError(
                "ComfyUI workflow must contain exactly one LoadImage node or "
                "titled LoadImage nodes for all inputs; "
                f"found {len(load_nodes)}"
            )

        output_nodes = tuple(
            str(node_id)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "SaveImage"
        )
        if not output_nodes:
            raise RuntimeError("ComfyUI workflow must contain at least one SaveImage node")
        for node_id in output_nodes:
            workflow[node_id].setdefault("inputs", {})["filename_prefix"] = output_prefix

        serialized = json.dumps(workflow, ensure_ascii=False)
        placeholders = sorted(set(re.findall(r"\$\{[^}]+\}", serialized)))
        if placeholders:
            raise RuntimeError(
                "ComfyUI workflow contains runtime placeholders: "
                + ", ".join(placeholders)
            )

        return output_nodes

    @staticmethod
    def _protect_source_structure(source_bytes: bytes, generated: Image.Image) -> Image.Image:
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        source = source.resize(generated.size, Image.Resampling.LANCZOS)

        source_y, _, _ = source.convert("YCbCr").split()
        _, generated_cb, generated_cr = generated.convert("YCbCr").split()
        colorized = Image.merge(
            "YCbCr",
            (source_y, generated_cb, generated_cr),
        ).convert("RGB")

        color_mask = source_y.point(
            lambda value: max(0, min(255, round((245 - value) * 255 / 80)))
        )
        colorized = Image.composite(colorized, source, color_mask)

        dark_mask = source_y.point(
            lambda value: (
                255
                if value <= 112
                else max(0, min(255, round((176 - value) * 255 / 64)))
            )
        )
        return Image.composite(source, colorized, dark_mask)

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
