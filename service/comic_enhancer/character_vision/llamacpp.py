from __future__ import annotations

import base64
from io import BytesIO
import json
import logging
import threading
import time
from typing import Any

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel

from ..logging_utils import log_operation
from ..networking import direct_http_client
from .contracts import (
    CharacterPageAnalysis,
    CharacterProfileAnalysis,
    CharacterVisionAnalyzer,
    PageCharacterInstance,
    PageCharacterMatch,
    UnmatchedPerson,
)
from .prompts import build_page_prompt, build_profile_prompt


logger = logging.getLogger(__name__)


class LlamaCppCharacterVisionAnalyzer(CharacterVisionAnalyzer):
    """通过 llama-server 的 OpenAI 兼容接口调用 Qwen3-VL。"""

    # 方法说明：初始化 sidecar 地址、鉴权、模型版本和请求限制。
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        deployment_revision: str,
        timeout_seconds: int,
        max_image_edge: int = 2048,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.deployment_revision = deployment_revision
        self.timeout_seconds = timeout_seconds
        self.max_image_edge = max(512, min(4096, max_image_edge))
        self._request_lock = threading.Lock()
        self._health_cache = (0.0, False)

    @property
    def model_revision(self) -> str:
        """返回配置锁定的 Qwen3-VL 部署版本。"""
        return f"{self.model_id}:{self.deployment_revision}"

    # 方法说明：校验 sidecar 健康状态以及实际加载的模型标识。
    def ready(self) -> bool:
        started = time.perf_counter()
        now = time.monotonic()
        cached_until, cached_value = self._health_cache
        if now < cached_until:
            return cached_value
        try:
            with direct_http_client(headers=self._headers(), timeout=3) as client:
                health = client.get(f"{self.base_url}/health")
                health.raise_for_status()
                models = client.get(f"{self.base_url}/v1/models")
                models.raise_for_status()
                identifiers = [
                    str(item.get("id", "")).lower()
                    for item in models.json().get("data", [])
                    if isinstance(item, dict) and str(item.get("id", "")).strip()
                ]
            expected = self.model_id.lower()
            ready = any(expected in identifier or identifier in expected for identifier in identifiers)
        except (httpx.HTTPError, TypeError, ValueError) as error:
            ready = False
            identifiers = []
            error_name = type(error).__name__
        else:
            error_name = ""
        self._health_cache = (now + (5 if ready else 1), ready)
        log_operation(
            logger,
            logging.INFO if ready else logging.WARNING,
            feature="Qwen sidecar健康检查",
            parameters={
                "base_url": self.base_url,
                "model_id": self.model_id,
            },
            result={
                "ready": ready,
                "models": len(identifiers),
                "error": error_name,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return ready

    # 方法说明：分析已确认参考图中的稳定特征和本地采色区域。
    def analyze_profile(
        self,
        *,
        character_id: str,
        display_name: str,
        image_bytes: bytes,
    ) -> CharacterProfileAnalysis:
        started = time.perf_counter()
        image_digest = _digest_prefix(image_bytes)
        content = [
            {"type": "text", "text": "Picture 1 = confirmed character reference."},
            {"type": "image_url", "image_url": {"url": self._image_data_url(image_bytes)}},
            {"type": "text", "text": build_profile_prompt(character_id, display_name)},
        ]
        value = self._completion(
            content,
            max_tokens=2048,
            response_model=CharacterProfileAnalysis,
        )
        result = CharacterProfileAnalysis.model_validate(value)
        if result.character_id != character_id:
            raise RuntimeError("Qwen3-VL 返回了错误的 character_id")
        log_operation(
            logger,
            logging.INFO,
            feature="Qwen角色档案分析",
            parameters={
                "character_id": character_id,
                "image_sha256": image_digest,
                "model_revision": self.model_revision,
            },
            result={
                "stable_traits": len(result.stable_traits),
                "outfit_traits": len(result.outfit_traits),
                "regions": len(result.regions),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return result

    # 方法说明：在当前漫画页中匹配最多三个候选角色并定位实例。
    def analyze_page(
        self,
        *,
        image_bytes: bytes,
        candidates: list[dict[str, object]],
    ) -> CharacterPageAnalysis:
        started = time.perf_counter()
        views = self._page_views(image_bytes)
        analyses = [
            (
                self._analyze_page_view(view_bytes, candidates),
                bounds,
                index,
            )
            for index, (view_bytes, bounds) in enumerate(
                views
            )
        ]
        merged = self._merge_page_analyses(analyses, candidates)
        log_operation(
            logger,
            logging.INFO,
            feature="Qwen漫画页角色分析",
            parameters={
                "image_sha256": _digest_prefix(image_bytes),
                "candidates": len(candidates),
                "views": len(views),
                "model_revision": self.model_revision,
            },
            result={
                "visible_characters": sum(
                    1 for match in merged.characters if match.visible
                ),
                "instances": sum(
                    len(match.instances) for match in merged.characters
                ),
                "unmatched_people": len(merged.unmatched_people),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return merged

    # 方法说明：分析一个完整页面或连续页面分段。
    def _analyze_page_view(
        self,
        image_bytes: bytes,
        candidates: list[dict[str, object]],
    ) -> CharacterPageAnalysis:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "Picture 1 = current grayscale manga page."},
            {"type": "image_url", "image_url": {"url": self._image_data_url(image_bytes)}},
        ]
        for picture_index, candidate in enumerate(candidates, start=2):
            content.extend(
                [
                    {
                        "type": "text",
                        "text": (
                            f"Picture {picture_index} = candidate reference_slot "
                            f"{candidate['reference_slot']}, character_id "
                            f"{candidate['character_id']}."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": self._image_data_url(bytes(candidate["image_bytes"]))
                        },
                    },
                ]
            )
        content.append({"type": "text", "text": build_page_prompt(candidates)})
        return CharacterPageAnalysis.model_validate(
            self._completion(
                content,
                max_tokens=1536,
                response_model=CharacterPageAnalysis,
            )
        )

    # 方法说明：将纵向漫画页拆成两个重叠分段以提高小人物和近景召回。
    def _page_views(
        self,
        image_bytes: bytes,
    ) -> list[tuple[bytes, tuple[int, int, int, int]]]:
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                page = ImageOps.exif_transpose(source).convert("RGB")
                width, height = page.size
                if height < width * 1.15:
                    return [(image_bytes, (0, 0, width, height))]
                bounds = [
                    (0, 0, width, round(height * 0.60)),
                    (0, round(height * 0.40), width, height),
                ]
                views = []
                for box in bounds:
                    output = BytesIO()
                    page.crop(box).save(output, format="PNG", optimize=True)
                    views.append((output.getvalue(), box))
                return views
        except (OSError, ValueError) as error:
            raise ValueError("Qwen3-VL 页面图片无效") from error

    # 方法说明：把分段 bbox 映射回原页并合并同角色结果。
    @staticmethod
    def _merge_page_analyses(
        analyses: list[
            tuple[CharacterPageAnalysis, tuple[int, int, int, int], int]
        ],
        candidates: list[dict[str, object]],
    ) -> CharacterPageAnalysis:
        page_width = max(bounds[2] for _, bounds, _ in analyses)
        page_height = max(bounds[3] for _, bounds, _ in analyses)
        matches: list[PageCharacterMatch] = []
        unmatched: list[UnmatchedPerson] = []
        for candidate in candidates:
            character_id = str(candidate["character_id"])
            slot = int(candidate["reference_slot"])
            instances: list[PageCharacterInstance] = []
            outfit_matches = False
            for analysis, bounds, view_index in analyses:
                match = next(
                    (
                        item
                        for item in analysis.characters
                        if item.character_id == character_id
                        and item.reference_slot == slot
                    ),
                    None,
                )
                if match is None or not match.visible:
                    continue
                outfit_matches = outfit_matches or match.outfit_matches_reference
                for instance in match.instances:
                    instances.append(
                        PageCharacterInstance(
                            panel_id=min(200, view_index * 50 + instance.panel_id),
                            box_2d=_map_box_to_page(
                                instance.box_2d,
                                bounds,
                                page_width,
                                page_height,
                            ),
                            confidence=instance.confidence,
                            match_evidence=instance.match_evidence,
                            counter_evidence=instance.counter_evidence,
                        )
                    )
            matches.append(
                PageCharacterMatch(
                    character_id=character_id,
                    reference_slot=slot,
                    visible=bool(instances),
                    outfit_matches_reference=outfit_matches,
                    instances=instances,
                )
            )
        for analysis, bounds, view_index in analyses:
            for person in analysis.unmatched_people:
                unmatched.append(
                    UnmatchedPerson(
                        panel_id=min(200, view_index * 50 + person.panel_id),
                        box_2d=_map_box_to_page(
                            person.box_2d,
                            bounds,
                            page_width,
                            page_height,
                        ),
                        reason=person.reason,
                    )
                )
        return CharacterPageAnalysis(
            characters=matches,
            unmatched_people=unmatched,
        )

    # 方法说明：调用聊天补全接口并解析严格 JSON 对象。
    def _completion(
        self,
        content: list[dict[str, Any]],
        *,
        max_tokens: int,
        response_model: type[BaseModel],
    ) -> dict[str, Any]:
        if not self.ready():
            raise RuntimeError("Qwen3-VL sidecar 未就绪或模型版本不匹配")
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "seed": 42,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            },
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=10)
        started = time.perf_counter()
        parameters = {
            "schema": response_model.__name__,
            "max_tokens": max_tokens,
            "images": sum(1 for item in content if item.get("type") == "image_url"),
        }
        try:
            with self._request_lock:
                with direct_http_client(
                    headers=self._headers(),
                    timeout=timeout,
                ) as client:
                    response = client.post(
                        f"{self.base_url}/v1/chat/completions",
                        json=payload,
                    )
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as error:
                        raise RuntimeError(
                            f"Qwen3-VL sidecar 返回 HTTP {response.status_code}"
                        ) from error
                    body = response.json()
            try:
                text = body["choices"][0]["message"]["content"]
                value = json.loads(text)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                raise RuntimeError("Qwen3-VL sidecar 没有返回有效 JSON 对象") from error
            if not isinstance(value, dict):
                raise RuntimeError("Qwen3-VL sidecar 返回值不是 JSON 对象")
        except Exception as error:
            log_operation(
                logger,
                logging.ERROR,
                feature="Qwen JSON补全请求",
                parameters=parameters,
                result={"status": "failed", "error": type(error).__name__},
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        log_operation(
            logger,
            logging.INFO,
            feature="Qwen JSON补全请求",
            parameters=parameters,
            result={"status": "success", "response_chars": len(text)},
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return value

    # 方法说明：将图片规范为最长边受限的 RGB PNG 数据地址。
    def _image_data_url(self, image_bytes: bytes) -> str:
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail(
                    (self.max_image_edge, self.max_image_edge),
                    Image.Resampling.LANCZOS,
                )
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
        except (OSError, ValueError) as error:
            raise ValueError("Qwen3-VL 输入图片无效") from error
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    # 方法说明：构造 sidecar Bearer 鉴权头。
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


# 方法说明：将页面分段中的千分比 bbox 映射为原页千分比坐标。
def _map_box_to_page(
    box: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
    page_width: int,
    page_height: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    view_width = right - left
    view_height = bottom - top
    x1, y1, x2, y2 = box
    return (
        max(0, min(1000, round((left + x1 * view_width / 1000) * 1000 / page_width))),
        max(0, min(1000, round((top + y1 * view_height / 1000) * 1000 / page_height))),
        max(0, min(1000, round((left + x2 * view_width / 1000) * 1000 / page_width))),
        max(0, min(1000, round((top + y2 * view_height / 1000) * 1000 / page_height))),
    )


# 方法说明：生成日志使用的输入内容摘要前缀。
def _digest_prefix(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()[:12]
