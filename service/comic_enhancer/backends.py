from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path
import re
import time
import uuid

import httpx
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from .models import CharacterMask, PageAnalysis, PanelRegion, ProcessOptions, ResolvedAdapter
from .workflows import WorkflowLoader


logger = logging.getLogger(__name__)


class InferenceBackend(ABC):
    name: str
    applies_adapters: bool = False
    supported_base_models: frozenset[str] = frozenset()
    model_profiles: tuple[str, ...] = ()

    def ready(self) -> bool:
        return True

    def reference_profile_ready(self) -> bool:
        return False

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
    processed_panels: int = 0
    model_profile: str = ""


@dataclass(frozen=True)
class InferenceAssets:
    image_bytes: bytes
    reference_bytes: bytes | None = None
    analysis: PageAnalysis | None = None
    character_references: dict[str, bytes] | None = None

    @property
    def has_panel_references(self) -> bool:
        if self.analysis is None or not self.character_references:
            return False
        accepted = {
            item.instance_id
            for item in self.analysis.characters
            if item.match.status == "accepted"
            and item.match.character_id in self.character_references
            and item.mask is not None
        }
        return any(
            accepted.intersection(panel.character_instance_ids)
            for panel in self.analysis.panels
        )


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
            if options.mode in {"quality", "manganinja"}:
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

    def reference_profile_ready(self) -> bool:
        return bool(
            self.workflow_loader.supports_reference() and self._reference_ready()
        )

    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        reference_available = self._reference_available(options, assets)
        workflow_revision = self.workflow_loader.revision(
            options,
            resolved,
            reference_available=reference_available,
        )
        if not reference_available or assets is None or assets.analysis is None:
            return workflow_revision
        analysis_hash = hashlib.sha256(
            assets.analysis.model_dump_json().encode("utf-8")
        ).hexdigest()
        reference_hashes = [
            f"{key}:{hashlib.sha256(value).hexdigest()}"
            for key, value in sorted((assets.character_references or {}).items())
        ]
        return ":".join([workflow_revision, analysis_hash, *reference_hashes])

    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=(
                "quality" if str(options.mode) == "manganinja" else str(options.mode)
            ),
        )

    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        reference_available = self._reference_available(options, assets)
        loaded_workflow = self.workflow_loader.load(
            options,
            resolved,
            reference_available=reference_available,
        )

        if loaded_workflow.reference_required and not assets.has_panel_references:
            raise RuntimeError("reference workflow requires matched panel characters")
        if loaded_workflow.reference_required:
            try:
                return self._process_reference_panels(
                    assets,
                    output_path,
                    loaded_workflow.prompt,
                    loaded_workflow.model_profile,
                )
            except Exception:
                logger.exception(
                    "MangaNinja 分格参考工作流失败，回退到主质量工作流"
                )
                loaded_workflow = self.workflow_loader.load(
                    options,
                    resolved,
                    reference_available=False,
                )
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

    def _process_reference_panels(
        self,
        assets: InferenceAssets,
        output_path: Path,
        workflow_template: dict,
        model_profile: str,
    ) -> InferenceOutcome:
        if assets.analysis is None or not assets.character_references:
            raise RuntimeError("panel analysis and character references are required")
        with Image.open(BytesIO(assets.image_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        composite = source.copy()
        characters = {item.instance_id: item for item in assets.analysis.characters}
        processed_panels = 0

        with httpx.Client(
            base_url=self.reference_base_url,
            timeout=self.timeout_seconds,
        ) as client:
            for panel in assets.analysis.panels:
                selected = [
                    characters[instance_id]
                    for instance_id in panel.character_instance_ids
                    if instance_id in characters
                    and characters[instance_id].match.status == "accepted"
                    and characters[instance_id].match.character_id
                    in assets.character_references
                    and characters[instance_id].mask is not None
                ]
                selected.sort(
                    key=lambda item: (
                        item.match.confidence,
                        (item.box.x2 - item.box.x1) * (item.box.y2 - item.box.y1),
                    ),
                    reverse=True,
                )
                selected = selected[:4]
                if not selected:
                    continue
                panel_bytes = self._crop_bytes(source, panel)
                reference_board, reference_points = self._reference_board(
                    selected,
                    assets.character_references,
                )
                target_points = self._target_points(selected, panel)
                generated = self._run_reference_prompt(
                    client,
                    panel_bytes,
                    reference_board,
                    reference_points,
                    target_points,
                    workflow_template,
                )
                restored = self._restore_geometry(panel_bytes, generated)
                protected = self._protect_masked_structure(panel_bytes, restored)
                panel_mask = self._panel_character_mask(selected, panel)
                composite.paste(
                    protected,
                    (panel.box.x1, panel.box.y1, panel.box.x2, panel.box.y2),
                    panel_mask,
                )
                processed_panels += 1

        if processed_panels == 0:
            raise RuntimeError("no panel has an accepted character reference")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        composite.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        return InferenceOutcome(
            adapter_applied=False,
            reference_applied=True,
            processed_panels=processed_panels,
            model_profile=model_profile,
        )

    def _run_reference_prompt(
        self,
        client: httpx.Client,
        panel_bytes: bytes,
        reference_bytes: bytes,
        reference_points: list[list[int]],
        target_points: list[list[int]],
        workflow_template: dict,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        comfy_inputs = {
            "INPUT_IMAGE": self._upload(client, self._pad_square(panel_bytes), "panel"),
            "REFERENCE_IMAGE": self._upload(
                client,
                reference_bytes,
                "character-board",
            ),
        }
        output_nodes = self._bind_io(
            workflow,
            input_images=comfy_inputs,
            output_prefix=f"comic-enhancer/panel-{uuid.uuid4().hex}",
        )
        self._bind_runtime_values(
            workflow,
            {
                "REFERENCE_POINTS": json.dumps(reference_points),
                "TARGET_POINTS": json.dumps(target_points),
            },
        )
        queued = client.post(
            "/prompt",
            json={"prompt": workflow, "client_id": uuid.uuid4().hex},
        )
        queued.raise_for_status()
        image_info = self._wait_for_output(
            client,
            queued.json()["prompt_id"],
            output_nodes,
        )
        result = client.get("/view", params=image_info)
        result.raise_for_status()
        with Image.open(BytesIO(result.content)) as generated_file:
            return ImageOps.exif_transpose(generated_file).convert("RGB").copy()

    @staticmethod
    def _crop_bytes(source: Image.Image, panel: PanelRegion) -> bytes:
        stream = BytesIO()
        source.crop(
            (panel.box.x1, panel.box.y1, panel.box.x2, panel.box.y2)
        ).save(stream, format="PNG")
        return stream.getvalue()

    @staticmethod
    def _reference_board(selected, references: dict[str, bytes]) -> tuple[bytes, list[list[int]]]:
        board = Image.new("RGB", (512, 512), "white")
        slot_width = 512 // len(selected)
        points: list[list[int]] = []
        for index, character in enumerate(selected):
            character_id = character.match.character_id
            with Image.open(BytesIO(references[character_id])) as reference_file:
                reference = ImageOps.contain(
                    ImageOps.exif_transpose(reference_file).convert("RGB"),
                    (slot_width - 8, 504),
                    Image.Resampling.LANCZOS,
                )
            left = index * slot_width + (slot_width - reference.width) // 2
            top = (512 - reference.height) // 2
            board.paste(reference, (left, top))
            # MangaNinja upstream writes the first coordinate as the matrix row.
            points.append([top + reference.height // 2, left + reference.width // 2])
        stream = BytesIO()
        board.save(stream, format="PNG", optimize=True)
        return stream.getvalue(), points

    @staticmethod
    def _target_points(selected, panel: PanelRegion) -> list[list[int]]:
        width = panel.box.x2 - panel.box.x1
        height = panel.box.y2 - panel.box.y1
        scale = min(512 / width, 512 / height)
        rendered_width = round(width * scale)
        rendered_height = round(height * scale)
        offset_x = (512 - rendered_width) // 2
        offset_y = (512 - rendered_height) // 2
        return [
            [
                max(0, min(511, round((item.box.center[1] - panel.box.y1) * scale) + offset_y)),
                max(0, min(511, round((item.box.center[0] - panel.box.x1) * scale) + offset_x)),
            ]
            for item in selected
        ]

    @staticmethod
    def _bind_runtime_values(workflow: dict, values: dict[str, str]) -> None:
        discovered: set[str] = set()
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            meta = node.get("_meta", {})
            role = str(meta.get("title", "")).strip().upper()
            input_name = str(meta.get("runtime_input", "")).strip()
            if role in values and input_name:
                node.setdefault("inputs", {})[input_name] = values[role]
                discovered.add(role)
        missing = sorted(set(values) - discovered)
        if missing:
            raise RuntimeError(
                "ComfyUI workflow is missing runtime value nodes: "
                + ", ".join(missing)
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

    def _reference_available(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
    ) -> bool:
        return bool(
            str(options.mode) == "manganinja"
            and assets is not None
            and assets.has_panel_references
            and self._reference_ready()
        )

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
    def _protect_masked_structure(source_bytes: bytes, generated: Image.Image) -> Image.Image:
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        generated = generated.resize(source.size, Image.Resampling.LANCZOS)

        source_y, _, _ = source.convert("YCbCr").split()
        _, generated_cb, generated_cr = generated.convert("YCbCr").split()
        neutral = Image.new("L", source.size, 128)
        chroma = ImageChops.lighter(
            ImageChops.difference(generated_cb, neutral),
            ImageChops.difference(generated_cr, neutral),
        )
        luminance_drop = chroma.point(lambda value: min(24, round(value * 0.3)))
        bright_mask = source_y.point(
            lambda value: max(0, min(255, round((value - 160) * 255 / 80)))
        )
        colored_y = ImageChops.subtract(
            source_y,
            ImageChops.multiply(luminance_drop, bright_mask),
        )
        colorized = Image.merge(
            "YCbCr",
            (colored_y, generated_cb, generated_cr),
        ).convert("RGB")
        ink_mask = source_y.point(
            lambda value: (
                255
                if value <= 80
                else max(0, min(255, round((128 - value) * 255 / 48)))
            )
        )
        return Image.composite(source, colorized, ink_mask)

    @staticmethod
    def _decode_character_mask(mask: CharacterMask) -> Image.Image:
        pixels = bytearray(mask.width * mask.height)
        offset = 0
        value = 0
        for count in mask.counts:
            if value:
                pixels[offset : offset + count] = b"\xff" * count
            offset += count
            value = 1 - value
        return Image.frombytes("L", (mask.width, mask.height), bytes(pixels))

    @staticmethod
    def _panel_character_mask(selected, panel: PanelRegion) -> Image.Image:
        width = panel.box.x2 - panel.box.x1
        height = panel.box.y2 - panel.box.y1
        panel_mask = Image.new("L", (width, height), 0)
        for character in selected:
            if character.mask is None:
                continue
            mask = ComfyUIBackend._decode_character_mask(character.mask)
            box_width = character.box.x2 - character.box.x1
            box_height = character.box.y2 - character.box.y1
            if mask.size != (box_width, box_height):
                continue
            positioned = Image.new("L", panel_mask.size, 0)
            positioned.paste(
                mask,
                (
                    character.box.x1 - panel.box.x1,
                    character.box.y1 - panel.box.y1,
                ),
            )
            panel_mask = ImageChops.lighter(
                panel_mask,
                positioned,
            )
        feather = min(1.5, min(panel_mask.size) * 0.005)
        return panel_mask.filter(ImageFilter.GaussianBlur(feather))

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
