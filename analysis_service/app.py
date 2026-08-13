from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from scipy.spatial.distance import cdist
from transformers import AutoModel

from .clustering import propagate_cluster_matches, stable_cross_page_clusters
from .matching import ranked_identity_candidates


MODEL_PATH = Path(os.environ.get("MAGIV2_MODEL_PATH", "/models/magiv2"))
MODEL_REVISION = os.environ.get(
    "MAGIV2_REVISION",
    "fbc890fec52977142e8ee00bfe26e9458b65517c",
)
MAX_PAGES = int(os.environ.get("MAGIV2_MAX_PAGES", "8"))
MAX_CHARACTERS = int(os.environ.get("MAGIV2_MAX_CHARACTERS", "16"))
MAX_DISTANCE = float(os.environ.get("MAGIV2_MAX_DISTANCE", "0.65"))
MIN_MARGIN = float(os.environ.get("MAGIV2_MIN_MARGIN", "0.08"))
CROSS_PAGE_CLUSTER_MAX_DISTANCE = float(
    os.environ.get("MAGIV2_CLUSTER_MAX_DISTANCE", "0.50")
)
PROPAGATION_MAX_DISTANCE = float(
    os.environ.get("MAGIV2_PROPAGATION_MAX_DISTANCE", "0.72")
)
ANALYZER_PROFILE = f"magiv2@{MODEL_REVISION[:12]}+cluster-v1+multi-view-v1"


class CharacterBankEntry(BaseModel):
    character_id: str
    name: str
    image_url: str
    provider: str = ""


class State:
    model = None


def read_image(image_bytes: bytes) -> np.ndarray:
    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("L").convert("RGB")
    return np.asarray(image)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not (MODEL_PATH / "MAGIv2.ready").is_file():
        raise RuntimeError(f"MAGIv2 ready marker is missing: {MODEL_PATH}")
    State.model = AutoModel.from_pretrained(
        MODEL_PATH,
        trust_remote_code=True,
        local_files_only=True,
    ).cuda().eval()
    yield
    State.model = None
    torch.cuda.empty_cache()


app = FastAPI(title="Comic Enhancer MAGIv2 Analyzer", lifespan=lifespan)


@app.get("/v1/health")
def health() -> dict[str, object]:
    return {
        "ready": State.model is not None and torch.cuda.is_available(),
        "profile": ANALYZER_PROFILE,
    }


@app.post("/v1/analyze/chapter")
async def analyze_chapter(
    pages: list[UploadFile] = File(),
    character_images: list[UploadFile] = File(default=[]),
    character_json: str = Form(default="[]"),
) -> dict[str, object]:
    if not 1 <= len(pages) <= MAX_PAGES:
        raise HTTPException(status_code=422, detail=f"pages must contain 1 to {MAX_PAGES} images")
    try:
        bank = [CharacterBankEntry.model_validate(item) for item in json.loads(character_json)]
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if len(bank) != len(character_images) or len(bank) > MAX_CHARACTERS:
        raise HTTPException(status_code=422, detail="character bank images do not match metadata")

    page_bytes = [await page.read() for page in pages]
    character_bytes = [await image.read() for image in character_images]
    if any(not value for value in [*page_bytes, *character_bytes]):
        raise HTTPException(status_code=422, detail="empty image")
    page_arrays = [read_image(value) for value in page_bytes]
    bank_arrays = [read_image(value) for value in character_bytes]

    with torch.no_grad():
        raw_results = State.model.predict_detections_and_associations(page_arrays)
        character_embeddings = character_crop_embeddings(page_arrays, raw_results)
        matches, candidates = match_characters(
            character_embeddings,
            raw_results,
            bank_arrays,
            bank,
        )
        page_embeddings = split_embeddings(character_embeddings, raw_results)
        page_labels = normalized_character_labels(raw_results)
        cluster_ids = stable_cross_page_clusters(
            page_labels,
            [embeddings.tolist() for embeddings in page_embeddings],
            max_distance=CROSS_PAGE_CLUSTER_MAX_DISTANCE,
        )
        matches = propagate_cluster_matches(
            matches,
            candidates,
            cluster_ids,
            max_distance=PROPAGATION_MAX_DISTANCE,
            min_margin=MIN_MARGIN,
        )

    pages_result = []
    for page_index, (image_bytes, image, raw, page_matches, page_cluster_ids) in enumerate(
        zip(page_bytes, page_arrays, raw_results, matches, cluster_ids)
    ):
        panels = normalized_panels(raw.get("panels", []), image.shape[1], image.shape[0])
        characters = []
        for character_index, (box, cluster_id, match) in enumerate(
            zip(
                raw.get("characters", []),
                page_cluster_ids,
                page_matches,
            )
        ):
            instance_id = f"p{page_index}-c{character_index}"
            character_box = box_payload(box, image.shape[1], image.shape[0])
            panel_index = containing_panel(character_box, panels)
            if panel_index is not None:
                character_box = intersect_box(character_box, panels[panel_index])
            characters.append(
                {
                    "instance_id": instance_id,
                    "cluster_id": cluster_id,
                    "box": character_box,
                    "panel_index": panel_index,
                    "match": match,
                }
            )
        pages_result.append(
            {
                "image_hash": hashlib.sha256(image_bytes).hexdigest(),
                "width": image.shape[1],
                "height": image.shape[0],
                "analyzer_profile": ANALYZER_PROFILE,
                "panels": [
                    {
                        "panel_index": index,
                        "box": panel,
                        "character_instance_ids": [
                            item["instance_id"]
                            for item in characters
                            if item["panel_index"] == index
                        ],
                    }
                    for index, panel in enumerate(panels)
                ],
                "characters": characters,
            }
        )
    return {"analyzer_profile": ANALYZER_PROFILE, "pages": pages_result}


def character_crop_embeddings(page_arrays, raw_results):
    character_boxes = [raw.get("characters", []) for raw in raw_results]
    if not any(character_boxes):
        return np.empty((0, 0), dtype=np.float32)
    embeddings = State.model.predict_crop_embeddings(page_arrays, character_boxes)
    non_empty = [value for value in embeddings if len(value)]
    if not non_empty:
        return np.empty((0, 0), dtype=np.float32)
    flattened = torch.cat(non_empty, dim=0)
    return torch.nn.functional.normalize(flattened, p=2, dim=1).cpu().numpy()


def split_embeddings(character_embeddings, raw_results):
    result = []
    offset = 0
    for raw in raw_results:
        count = len(raw.get("characters", []))
        result.append(character_embeddings[offset : offset + count])
        offset += count
    return result


def normalized_character_labels(raw_results):
    result = []
    for raw in raw_results:
        count = len(raw.get("characters", []))
        labels = list(raw.get("character_cluster_labels", []))
        if len(labels) != count:
            labels = list(range(count))
        result.append(labels)
    return result


def match_characters(character_embeddings, raw_results, bank_arrays, bank):
    counts = [len(raw.get("characters", [])) for raw in raw_results]
    total = sum(counts)
    if total == 0:
        empty = [[] for _ in raw_results]
        return empty, empty
    if not bank_arrays:
        rejected = {
            "status": "rejected",
            "confidence": 0,
            "reason": "character bank is empty",
        }
        matches = [[dict(rejected) for _ in range(count)] for count in counts]
        candidates = [[None for _ in range(count)] for count in counts]
        return matches, candidates

    bank_embeddings = State.model.predict_crop_embeddings(
        bank_arrays,
        [[[0, 0, image.shape[1], image.shape[0]]] for image in bank_arrays],
    )
    bank_embeddings = torch.cat(bank_embeddings, dim=0)
    bank_embeddings = torch.nn.functional.normalize(bank_embeddings, p=2, dim=1).cpu().numpy()
    distances = cdist(character_embeddings, bank_embeddings)

    flat = []
    flat_candidates = []
    for row in distances:
        identity_candidates = ranked_identity_candidates(row, bank)
        best, best_index = identity_candidates[0]
        second = identity_candidates[1][0] if len(identity_candidates) > 1 else 2.0
        margin = max(0.0, second - best)
        accepted = best <= MAX_DISTANCE and (
            len(identity_candidates) == 1 or margin >= MIN_MARGIN
        )
        entry = bank[best_index]
        candidate = {
            "character_id": entry.character_id,
            "character_name": entry.name,
            "reference_url": entry.image_url,
            "best_distance": best,
            "second_distance": second,
            "margin": margin,
        }
        flat_candidates.append(candidate)
        confidence = max(0.0, min(1.0, 1.0 - best)) if accepted else 0.0
        flat.append(
            {
                "character_id": entry.character_id if accepted else None,
                "character_name": entry.name if accepted else "",
                "reference_url": entry.image_url if accepted else None,
                "status": "accepted" if accepted else "rejected",
                "confidence": confidence,
                "best_distance": best,
                "second_distance": second,
                "margin": margin,
                "reason": "" if accepted else (
                    "best candidate is too far"
                    if best > MAX_DISTANCE
                    else "top candidates are ambiguous"
                ),
            }
        )
    result = []
    candidate_result = []
    offset = 0
    for count in counts:
        result.append(flat[offset : offset + count])
        candidate_result.append(flat_candidates[offset : offset + count])
        offset += count
    return result, candidate_result


def normalized_panels(raw_panels, width: int, height: int):
    panels = [box_payload(box, width, height) for box in raw_panels]
    if not panels:
        return [{"x1": 0, "y1": 0, "x2": width, "y2": height}]
    return panels[:12]


def box_payload(box, width: int, height: int):
    x1, y1, x2, y2 = [round(float(value)) for value in box]
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def containing_panel(character_box, panels) -> int | None:
    cx = (character_box["x1"] + character_box["x2"]) / 2
    cy = (character_box["y1"] + character_box["y2"]) / 2
    candidates = [
        (index, (panel["x2"] - panel["x1"]) * (panel["y2"] - panel["y1"]))
        for index, panel in enumerate(panels)
        if panel["x1"] <= cx <= panel["x2"] and panel["y1"] <= cy <= panel["y2"]
    ]
    return min(candidates, key=lambda item: item[1])[0] if candidates else None


def intersect_box(box, panel):
    return {
        "x1": max(box["x1"], panel["x1"]),
        "y1": max(box["y1"], panel["y1"]),
        "x2": min(box["x2"], panel["x2"]),
        "y2": min(box["y2"], panel["y2"]),
    }
