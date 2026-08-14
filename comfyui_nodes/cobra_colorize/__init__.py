from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

import numpy as np
from aiohttp import web
from PIL import Image
from server import PromptServer
import torch


_COBRA_SOCKET = Path("/tmp/comic-enhancer-cobra.sock")
_COBRA_PYTHON = Path("/opt/cobra-venv/bin/python")
_COBRA_WORKER = Path(__file__).with_name("cobra_worker.py")
_COBRA_PROCESS: subprocess.Popen | None = None
_COBRA_EXECUTION_LOCK = threading.Lock()


def _tensor_to_image(value: torch.Tensor) -> Image.Image:
    array = value[0].detach().cpu().numpy()
    array = (array.clip(0, 1) * 255).round().astype(np.uint8)
    return Image.fromarray(array, "RGB")


def _image_to_tensor(value: Image.Image) -> torch.Tensor:
    array = np.asarray(value.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(array)[None, ...]


def _request_worker(payload: dict, timeout_seconds: float) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout_seconds)
        connection.connect(str(_COBRA_SOCKET))
        connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
    if not response:
        raise RuntimeError("Cobra worker closed the connection without a response")
    result = json.loads(response.decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Cobra worker failed")
    return result


def _worker_ready() -> bool:
    try:
        _request_worker({"action": "ping"}, 1)
        return True
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False


def _ensure_worker() -> None:
    global _COBRA_PROCESS
    if _worker_ready():
        return
    if not _COBRA_PYTHON.is_file():
        raise RuntimeError(f"Cobra Python environment not found: {_COBRA_PYTHON}")
    if not _COBRA_WORKER.is_file():
        raise RuntimeError(f"Cobra worker not found: {_COBRA_WORKER}")
    if _COBRA_PROCESS is not None and _COBRA_PROCESS.poll() is None:
        raise RuntimeError("Cobra worker is running but not accepting requests")
    _COBRA_SOCKET.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    _COBRA_PROCESS = subprocess.Popen(
        [_COBRA_PYTHON, _COBRA_WORKER, "--socket", str(_COBRA_SOCKET)],
        cwd="/opt/cobra",
        env=environment,
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if _COBRA_PROCESS.poll() is not None:
            raise RuntimeError(
                f"Cobra worker exited during startup with code {_COBRA_PROCESS.returncode}"
            )
        if _worker_ready():
            return
        time.sleep(0.5)
    _COBRA_PROCESS.terminate()
    raise RuntimeError("Cobra worker startup timed out")


def _shutdown_worker() -> bool:
    global _COBRA_PROCESS
    with _COBRA_EXECUTION_LOCK:
        if not _worker_ready():
            _COBRA_SOCKET.unlink(missing_ok=True)
            return False
        _request_worker({"action": "shutdown"}, 10)
        if _COBRA_PROCESS is not None:
            try:
                _COBRA_PROCESS.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _COBRA_PROCESS.terminate()
            _COBRA_PROCESS = None
        _COBRA_SOCKET.unlink(missing_ok=True)
        return True


@PromptServer.instance.routes.post("/comic-enhancer/cobra/unload")
async def unload_cobra_worker(_request):
    return web.json_response({"released": _shutdown_worker()})


class CobraColorize:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "reference_1": ("IMAGE",),
                "reference_2": ("IMAGE",),
                "reference_3": ("IMAGE",),
                "reference_4": ("IMAGE",),
                "reference_5": ("IMAGE",),
                "reference_6": ("IMAGE",),
                "reference_7": ("IMAGE",),
                "reference_8": ("IMAGE",),
                "reference_9": ("IMAGE",),
                "reference_10": ("IMAGE",),
                "reference_11": ("IMAGE",),
                "reference_12": ("IMAGE",),
                "reference_count": ("INT", {"default": 3, "min": 1, "max": 12}),
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
        reference_4: torch.Tensor,
        reference_5: torch.Tensor,
        reference_6: torch.Tensor,
        reference_7: torch.Tensor,
        reference_8: torch.Tensor,
        reference_9: torch.Tensor,
        reference_10: torch.Tensor,
        reference_11: torch.Tensor,
        reference_12: torch.Tensor,
        reference_count: int,
        seed: int,
        steps: int,
        top_k: int,
        style: str,
    ):
        with _COBRA_EXECUTION_LOCK:
            _ensure_worker()
            with tempfile.TemporaryDirectory(prefix="comfyui-cobra-") as temp_dir:
                temp_root = Path(temp_dir)
                image_path = temp_root / "input.png"
                output_path = temp_root / "output.png"
                _tensor_to_image(image).save(image_path, format="PNG")
                reference_paths = []
                references = (
                    reference_1,
                    reference_2,
                    reference_3,
                    reference_4,
                    reference_5,
                    reference_6,
                    reference_7,
                    reference_8,
                    reference_9,
                    reference_10,
                    reference_11,
                    reference_12,
                )
                for index, reference in enumerate(references[:reference_count], 1):
                    path = temp_root / f"reference-{index:02d}.png"
                    _tensor_to_image(reference).save(path, format="PNG")
                    reference_paths.append(str(path))
                _request_worker(
                    {
                        "action": "colorize",
                        "image": str(image_path),
                        "references": reference_paths,
                        "output": str(output_path),
                        "seed": seed,
                        "steps": steps,
                        "top_k": top_k,
                        "style": style,
                    },
                    900,
                )
                if not output_path.is_file():
                    raise RuntimeError("Cobra worker did not create an output image")
                with Image.open(output_path) as generated:
                    result = generated.convert("RGB").copy()
        return (_image_to_tensor(result),)


NODE_CLASS_MAPPINGS = {"CobraColorize": CobraColorize}
NODE_DISPLAY_NAME_MAPPINGS = {"CobraColorize": "Cobra multi-reference colorization"}
