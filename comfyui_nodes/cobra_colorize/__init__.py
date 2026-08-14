from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageOps
import torch


_COBRA_APP = None
_COBRA_EXECUTION_LOCK = threading.Lock()


def _load_cobra_app():
    global _COBRA_APP
    if _COBRA_APP is None:
        import types

        source = Path("/opt/cobra/app.py")
        if str(source.parent) not in sys.path:
            sys.path.insert(0, str(source.parent))
        if not source.is_file():
            raise RuntimeError(f"Cobra source not found: {source}")
        source_text = source.read_text(encoding="utf-8")
        source_text = source_text.split("\nwith gr.Blocks() as demo:", 1)[0]
        module = types.ModuleType("cobra_upstream_app")
        module.__file__ = str(source)
        exec(compile(source_text, str(source), "exec"), module.__dict__)
        _COBRA_APP = module
    return _COBRA_APP


def _tensor_to_image(value: torch.Tensor) -> Image.Image:
    array = value[0].detach().cpu().numpy()
    array = (array.clip(0, 1) * 255).round().astype(np.uint8)
    return Image.fromarray(array, "RGB")


def _image_to_tensor(value: Image.Image) -> torch.Tensor:
    array = np.asarray(value.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(array)[None, ...]


class CobraColorize:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "reference_1": ("IMAGE",),
                "reference_2": ("IMAGE",),
                "reference_3": ("IMAGE",),
                "seed": ("INT", {"default": 20260814, "min": 0, "max": 2**31 - 1}),
                "steps": ("INT", {"default": 10, "min": 1, "max": 30}),
                "top_k": ("INT", {"default": 3, "min": 1, "max": 20}),
                "style": (
                    "STRING",
                    {"default": "line + shadow", "multiline": False},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "colorize"
    CATEGORY = "Comic Enhancer/Cobra"

    def colorize(
        self,
        image: torch.Tensor,
        reference_1: torch.Tensor,
        reference_2: torch.Tensor,
        reference_3: torch.Tensor,
        seed: int,
        steps: int,
        top_k: int,
        style: str,
    ):
        # Cobra's upstream pipeline resolves prompt_tensor and other assets from
        # the process working directory. Keep this global cwd change serialized so
        # parallel ComfyUI executions cannot observe a partially switched path.
        with _COBRA_EXECUTION_LOCK:
            previous_cwd = os.getcwd()
            os.chdir("/opt/cobra")
            try:
                cobra = _load_cobra_app()
                page = _tensor_to_image(image)
                references = [
                    _tensor_to_image(reference_1),
                    _tensor_to_image(reference_2),
                    _tensor_to_image(reference_3),
                ]
                with tempfile.TemporaryDirectory(prefix="comfyui-cobra-") as temp_dir:
                    files = []
                    for index, reference in enumerate(references, 1):
                        path = Path(temp_dir) / f"reference-{index:02d}.png"
                        reference.save(path, format="PNG")
                        files.append(SimpleNamespace(name=str(path)))
                    (
                        extracted,
                        hint_color,
                        hint_mask,
                        query_origin,
                        extracted_origin,
                        resolution,
                    ) = cobra.extract_sketch_line_image(page, style)
                    gallery = cobra.colorize_image(
                        extracted,
                        files,
                        resolution,
                        seed,
                        steps,
                        top_k,
                        hint_mask,
                        hint_color,
                        query_origin,
                        extracted_origin,
                    )
            finally:
                os.chdir(previous_cwd)
        if not gallery:
            raise RuntimeError("Cobra returned no image")
        return (_image_to_tensor(ImageOps.exif_transpose(gallery[0])),)


NODE_CLASS_MAPPINGS = {"CobraColorize": CobraColorize}
NODE_DISPLAY_NAME_MAPPINGS = {"CobraColorize": "Cobra 多参考上色"}
