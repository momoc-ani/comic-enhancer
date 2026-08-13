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

    def ready(self) -> bool:
        return True

    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> str:
        return self.name

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
        workflow_loader: WorkflowLoader,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.workflow_loader = workflow_loader

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
    ) -> str:
        return self.workflow_loader.revision(options, resolved)

    def process(
        self,
        image_bytes: bytes,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        loaded_workflow = self.workflow_loader.load(options, resolved)

        upload_name = f"comic-enhancer-{uuid.uuid4().hex}.png"
        normalized = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source:
            ImageOps.exif_transpose(source).convert("RGB").save(normalized, format="PNG")
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            upload = client.post(
                "/upload/image",
                files={"image": (upload_name, normalized.getvalue(), "image/png")},
                data={"type": "input", "overwrite": "true"},
            )
            upload.raise_for_status()
            uploaded = upload.json()
            comfy_input = self._comfy_path(uploaded)

            workflow = loaded_workflow.prompt
            output_nodes = self._bind_io(
                workflow,
                input_image=comfy_input,
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
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        return InferenceOutcome(adapter_applied=loaded_workflow.adapter_applied)

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
        input_image: str,
        output_prefix: str,
    ) -> tuple[str, ...]:
        load_nodes = [
            (str(node_id), node)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "LoadImage"
        ]
        if len(load_nodes) != 1:
            raise RuntimeError(
                "ComfyUI workflow must contain exactly one LoadImage node; "
                f"found {len(load_nodes)}"
            )
        load_nodes[0][1].setdefault("inputs", {})["image"] = input_image

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
