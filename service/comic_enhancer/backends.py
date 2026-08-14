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
REFERENCE_PROCESSING_REVISION = "quality-base-reference-overlay-v5"
CHARACTER_ANCHOR_FRACTIONS = (0.18, 0.40, 0.65, 0.82)
REFERENCE_CHROMA_SCALE = 0.62
REFERENCE_CHROMA_LIMIT = 64


class InferenceBackend(ABC):
    name: str
    applies_adapters: bool = False
    supported_base_models: frozenset[str] = frozenset()
    model_profiles: tuple[str, ...] = ()

    def ready(self) -> bool:
        return True

    def reference_profile_ready(self) -> bool:
        return False

    def cobra_profile_ready(self) -> bool:
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
    model_profiles = ("sd15-colorize", "manganinja-reference", "cobra")

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
        cobra_base_url: str | None = None,
        cobra_enabled: bool = False,
        cobra_workflow: Path | None = None,
        cobra_reference_limit: int = 3,
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
        self.cobra_base_url = (cobra_base_url or "").rstrip("/")
        self.cobra_enabled = cobra_enabled and bool(self.cobra_base_url)
        self.cobra_workflow = cobra_workflow
        self.cobra_reference_limit = max(1, min(12, cobra_reference_limit))
        self._cobra_ready_cached_until = 0.0
        self._cobra_ready_cached_value = False

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

    def cobra_profile_ready(self) -> bool:
        if not self.cobra_enabled or not self.workflow_loader.supports_cobra():
            return False
        now = time.monotonic()
        if now < self._cobra_ready_cached_until:
            return self._cobra_ready_cached_value
        try:
            response = httpx.get(f"{self.cobra_base_url}/system_stats", timeout=2)
            ready = response.status_code == 200
        except httpx.HTTPError:
            ready = False
        self._cobra_ready_cached_value = ready
        self._cobra_ready_cached_until = now + (5 if ready else 1)
        return ready

    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        if str(options.mode) == "cobra":
            reference_hashes = []
            if assets is not None:
                reference_hashes = sorted(
                    hashlib.sha256(value).hexdigest()
                    for value in (assets.character_references or {}).values()
                )
            workflow_revision = self.workflow_loader.revision(
                options,
                resolved,
                reference_available=False,
            )
            return ":".join([workflow_revision, *reference_hashes])
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
        base_workflow_revision = self.workflow_loader.revision(
            options,
            resolved,
            reference_available=False,
        )
        return ":".join(
            [
                workflow_revision,
                base_workflow_revision,
                REFERENCE_PROCESSING_REVISION,
                analysis_hash,
                *reference_hashes,
            ]
        )

    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=(
                "quality"
                if str(options.mode) in {"manganinja", "cobra"}
                else str(options.mode)
            ),
        )

    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if str(options.mode) == "cobra":
            try:
                return self._process_cobra(assets, output_path, resolved)
            except Exception:
                logger.exception("Cobra 实验档失败，回退到质量工作流")
                quality_options = options.model_copy(update={"mode": "quality"})
                return self.process(assets, output_path, quality_options, resolved)
        reference_available = self._reference_available(options, assets)
        loaded_workflow = self.workflow_loader.load(
            options,
            resolved,
            reference_available=reference_available,
        )

        if loaded_workflow.reference_required and not assets.has_panel_references:
            raise RuntimeError("reference workflow requires matched panel characters")
        if loaded_workflow.reference_required:
            base_workflow = self.workflow_loader.load(
                options,
                resolved,
                reference_available=False,
            )
            base_generated = self._run_page_prompt(
                self.base_url,
                assets.image_bytes,
                base_workflow.prompt,
            )
            base_image = self._protect_source_structure(
                assets.image_bytes,
                base_generated,
            )
            try:
                return self._process_reference_panels(
                    assets,
                    output_path,
                    loaded_workflow.prompt,
                    loaded_workflow.model_profile,
                    base_image,
                    base_workflow.adapter_applied,
                )
            except Exception:
                logger.exception(
                    "MangaNinja 分格参考工作流失败，回退到主质量工作流"
                )
                self._save_output(base_image, output_path)
                return InferenceOutcome(
                    adapter_applied=base_workflow.adapter_applied,
                    model_profile=base_workflow.model_profile,
                )
        generated = self._run_page_prompt(
            self.base_url,
            assets.image_bytes,
            loaded_workflow.prompt,
        )
        image = self._protect_source_structure(assets.image_bytes, generated)
        self._save_output(image, output_path)
        return InferenceOutcome(
            adapter_applied=loaded_workflow.adapter_applied,
            model_profile=loaded_workflow.model_profile,
        )

    def _process_cobra(
        self,
        assets: InferenceAssets,
        output_path: Path,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if not self.cobra_profile_ready():
            raise RuntimeError("Cobra 服务未就绪")
        references = self._cobra_reference_images(assets)
        if not references:
            raise RuntimeError("Cobra 需要至少一张角色参考图")
        if self.cobra_workflow is None:
            raise RuntimeError("Cobra 工作流未配置")
        loaded_workflow = self.workflow_loader.load(
            ProcessOptions(mode="cobra"),
            resolved,
        )
        generated = self._run_cobra_prompt(
            assets.image_bytes,
            references,
            loaded_workflow.prompt,
        )
        generated = self._restore_geometry(assets.image_bytes, generated)
        self._save_output(
            self._protect_cobra_structure(assets.image_bytes, generated),
            output_path,
        )
        return InferenceOutcome(
            adapter_applied=False,
            reference_applied=True,
            model_profile="cobra",
        )

    def _cobra_reference_images(self, assets: InferenceAssets) -> list[bytes]:
        candidates: list[bytes] = []
        if assets.reference_bytes is not None:
            candidates.append(assets.reference_bytes)
        candidates.extend((assets.character_references or {}).values())
        unique: list[bytes] = []
        seen: set[str] = set()
        for value in candidates:
            digest = hashlib.sha256(value).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(value)
            if len(unique) >= self.cobra_reference_limit:
                break
        return unique

    def _run_cobra_prompt(
        self,
        image_bytes: bytes,
        references: list[bytes],
        workflow_template: dict,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        with httpx.Client(base_url=self.cobra_base_url, timeout=self.timeout_seconds) as client:
            input_images = {
                "INPUT_IMAGE": self._upload(client, image_bytes, "page"),
            }
            for index in range(1, 4):
                input_images[f"REFERENCE_IMAGE_{index}"] = self._upload(
                    client,
                    references[min(index - 1, len(references) - 1)],
                    f"reference-{index}",
                )
            output_nodes = self._bind_io(
                workflow,
                input_images=input_images,
                output_prefix=f"comic-enhancer/cobra-{uuid.uuid4().hex}",
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
    def _protect_cobra_structure(source_bytes: bytes, generated: Image.Image) -> Image.Image:
        """Keep only near-black lettering/ink and white bubble paper from tinting."""
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        source = source.resize(generated.size, Image.Resampling.LANCZOS)
        source_y, _, _ = source.convert("YCbCr").split()
        _, generated_cb, generated_cr = generated.convert("YCbCr").split()
        colorized = Image.merge(
            "YCbCr",
            (source_y, generated_cb, generated_cr),
        ).convert("RGB")
        ink_mask = source_y.point(
            lambda value: (
                255
                if value <= 52
                else max(0, min(180, round((84 - value) * 180 / 32)))
            )
        )
        paper_mask = source_y.point(
            lambda value: 255 if value >= 248 else max(0, round((value - 232) * 255 / 16))
        )
        structure_mask = ImageChops.lighter(ink_mask, paper_mask)
        return Image.composite(source, colorized, structure_mask)

    def _run_page_prompt(
        self,
        base_url: str,
        image_bytes: bytes,
        workflow_template: dict,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        with httpx.Client(base_url=base_url, timeout=self.timeout_seconds) as client:
            comfy_inputs = {
                "INPUT_IMAGE": self._upload(client, image_bytes, "page"),
            }
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
        with Image.open(BytesIO(result.content)) as source:
            return ImageOps.exif_transpose(source).convert("RGB").copy()

    @staticmethod
    def _save_output(image: Image.Image, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        image.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)

    def _process_reference_panels(
        self,
        assets: InferenceAssets,
        output_path: Path,
        workflow_template: dict,
        model_profile: str,
        base_image: Image.Image,
        base_adapter_applied: bool,
    ) -> InferenceOutcome:
        if assets.analysis is None or not assets.character_references:
            raise RuntimeError("panel analysis and character references are required")
        with Image.open(BytesIO(assets.image_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        composite = base_image.convert("RGB").resize(
            source.size,
            Image.Resampling.LANCZOS,
        )
        characters = {item.instance_id: item for item in assets.analysis.characters}
        processed_panel_indexes: set[int] = set()

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
                # A full panel can make a small character too weak for reference-color
                # transfer. Process one focused character region at a time, then only
                # composite pixels accepted by that character's SAM mask.
                for character in reversed(selected):
                    focus = self._character_focus_region(character, panel)
                    focus_bytes = self._crop_bytes(source, focus)
                    target_points = self._target_points([character], focus)
                    reference_bytes = self._character_reference(
                        character,
                        panel,
                        assets.character_references,
                    )
                    reference_board, reference_points = self._reference_board(
                        [character],
                        {character.match.character_id: reference_bytes},
                        anchors_per_character=len(target_points),
                    )
                    generated = self._run_reference_prompt(
                        client,
                        focus_bytes,
                        reference_board,
                        reference_points,
                        target_points,
                        workflow_template,
                    )
                    restored = self._restore_geometry(focus_bytes, generated)
                    protected = self._protect_masked_structure(focus_bytes, restored)
                    character_mask = self._panel_character_mask([character], focus)
                    composite.paste(
                        protected,
                        (focus.box.x1, focus.box.y1, focus.box.x2, focus.box.y2),
                        character_mask,
                    )
                    processed_panel_indexes.add(panel.panel_index)

        if not processed_panel_indexes:
            raise RuntimeError("no panel has an accepted character reference")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        composite.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        return InferenceOutcome(
            adapter_applied=base_adapter_applied,
            reference_applied=True,
            processed_panels=len(processed_panel_indexes),
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
    def _character_focus_region(character, panel: PanelRegion) -> PanelRegion:
        width = character.box.x2 - character.box.x1
        height = character.box.y2 - character.box.y1
        padding_x = max(24, round(width * 0.6))
        padding_y = max(24, round(height * 0.25))
        return PanelRegion(
            panel_index=panel.panel_index,
            box={
                "x1": max(panel.box.x1, character.box.x1 - padding_x),
                "y1": max(panel.box.y1, character.box.y1 - padding_y),
                "x2": min(panel.box.x2, character.box.x2 + padding_x),
                "y2": min(panel.box.y2, character.box.y2 + padding_y),
            },
            character_instance_ids=[character.instance_id],
        )

    @staticmethod
    def _character_reference(character, panel, references: dict[str, bytes]) -> bytes:
        character_id = character.match.character_id
        width = character.box.x2 - character.box.x1
        height = character.box.y2 - character.box.y1
        panel_width = panel.box.x2 - panel.box.x1
        is_portrait = height / max(1, width) < 1.4 or width / panel_width >= 0.8
        variant = "portrait" if is_portrait else "full-body"
        return references.get(f"{character_id}:{variant}") or references[character_id]

    @staticmethod
    def _reference_board(
        selected,
        references: dict[str, bytes],
        *,
        anchors_per_character: int = 4,
    ) -> tuple[bytes, list[list[int]]]:
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
            points.extend(
                ComfyUIBackend._vertical_anchor_points(
                    center_x=left + reference.width // 2,
                    top=top,
                    height=reference.height,
                    count=anchors_per_character,
                )
            )
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
        points: list[list[int]] = []
        for item in selected:
            top = round((item.box.y1 - panel.box.y1) * scale) + offset_y
            bottom = round((item.box.y2 - panel.box.y1) * scale) + offset_y
            center_x = round((item.box.center[0] - panel.box.x1) * scale) + offset_x
            anchors = ComfyUIBackend._vertical_anchor_points(
                center_x=center_x,
                top=top,
                height=max(1, bottom - top),
                count=4,
            )
            points.extend(anchors if len(anchors) == 4 else [[(top + bottom) // 2, center_x]])
        return points

    @staticmethod
    def _vertical_anchor_points(
        *,
        center_x: int,
        top: int,
        height: int,
        count: int,
    ) -> list[list[int]]:
        if count == 1:
            fractions = (0.5,)
        elif count == 4:
            fractions = CHARACTER_ANCHOR_FRACTIONS
        else:
            raise ValueError("character anchors must contain 1 or 4 points")
        points = [
            [
                max(0, min(511, round(top + height * fraction))),
                max(0, min(511, center_x)),
            ]
            for fraction in fractions
        ]
        return points if len({tuple(point) for point in points}) == len(points) else []

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
        generated_cb, generated_cr = ComfyUIBackend._limit_reference_chroma(
            generated_cb,
            generated_cr,
        )
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
    def _limit_reference_chroma(
        generated_cb: Image.Image,
        generated_cr: Image.Image,
    ) -> tuple[Image.Image, Image.Image]:
        """Reduce reference color overshoot without flattening ordinary anime colors."""
        neutral = 128

        def limit(value: int) -> int:
            delta = value - neutral
            delta = round(delta * REFERENCE_CHROMA_SCALE)
            delta = max(-REFERENCE_CHROMA_LIMIT, min(REFERENCE_CHROMA_LIMIT, delta))
            return neutral + delta

        return (
            generated_cb.point(limit),
            generated_cr.point(limit),
        )

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
