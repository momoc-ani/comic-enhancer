from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps

import app as cobra


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REFERENCES = 12
INFERENCE_LOCK = threading.Lock()

api = FastAPI(title="Cobra Candidate API", version="0.1.0")


def _decode_image(data: bytes, label: str) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as source:
            return ImageOps.exif_transpose(source).convert("RGB").copy()
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"{label} 不是有效图片") from error


async def _read_upload(upload: UploadFile, label: str) -> bytes:
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail=f"{label} 不能为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} 超过 20 MiB")
    return data


def _infer(
    image_bytes: bytes,
    reference_bytes: list[bytes],
    *,
    style: str,
    seed: int,
    steps: int,
    top_k: int,
) -> tuple[bytes, int]:
    image = _decode_image(image_bytes, "漫画页")
    references = [
        _decode_image(data, f"参考图 {index}")
        for index, data in enumerate(reference_bytes, 1)
    ]
    started = time.perf_counter()
    with INFERENCE_LOCK, tempfile.TemporaryDirectory(prefix="cobra-api-") as temp_dir:
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
        ) = cobra.extract_sketch_line_image(image, style)
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
        output = BytesIO()
        gallery[0].save(output, format="PNG", optimize=True)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return output.getvalue(), elapsed_ms


@api.get("/v1/health")
def health() -> dict[str, object]:
    model_path = Path(os.environ.get("COBRA_MODEL_PATH", ""))
    pixart_path = Path(os.environ.get("COBRA_PIXART_MODEL_PATH", ""))
    return {
        "ready": model_path.is_dir() and pixart_path.is_dir(),
        "model": "cobra",
        "style": cobra.cur_style,
    }


@api.post("/v1/colorize")
async def colorize(
    image: UploadFile = File(...),
    references: list[UploadFile] = File(...),
    style: str = Form("line + shadow"),
    seed: int = Form(20260814),
    steps: int = Form(10),
    top_k: int = Form(3),
) -> Response:
    if style not in {"line", "line + shadow"}:
        raise HTTPException(status_code=400, detail="style 只支持 line 或 line + shadow")
    if not 1 <= len(references) <= MAX_REFERENCES:
        raise HTTPException(status_code=400, detail="参考图数量必须为 1 到 12 张")
    if not 1 <= steps <= 30:
        raise HTTPException(status_code=400, detail="steps 必须为 1 到 30")
    if not 1 <= top_k <= 20:
        raise HTTPException(status_code=400, detail="top_k 必须为 1 到 20")

    image_bytes = await _read_upload(image, "漫画页")
    reference_bytes = [
        await _read_upload(reference, f"参考图 {index}")
        for index, reference in enumerate(references, 1)
    ]
    result, elapsed_ms = await run_in_threadpool(
        _infer,
        image_bytes,
        reference_bytes,
        style=style,
        seed=seed,
        steps=steps,
        top_k=top_k,
    )
    return Response(
        content=result,
        media_type="image/png",
        headers={
            "X-Comic-Model": "cobra",
            "X-Inference-Ms": str(elapsed_ms),
        },
    )
