from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
import hashlib
import json
import logging
import re
import time
import uuid

import httpx
from PIL import Image, ImageOps


logger = logging.getLogger(__name__)
WorkflowMutator = Callable[[dict], None]


class ComfyUITransport:
    """封装 ComfyUI 上传、提交、轮询和结果下载。"""

    # 方法说明：初始化 ComfyUI 地址、超时和轮询参数。
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._profile_ready_cache: dict[str, tuple[float, bool]] = {}

    # 方法说明：检查 ComfyUI 基础服务是否可以响应。
    def ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/system_stats", timeout=2)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # 方法说明：按档位短暂缓存 ComfyUI 与工作流可用性检查结果。
    def profile_ready(
        self,
        cache_key: str,
        *,
        enabled: bool,
        workflow_supported: bool,
    ) -> bool:
        if not enabled or not workflow_supported:
            return False
        now = time.monotonic()
        cached_until, cached_value = self._profile_ready_cache.get(
            cache_key,
            (0.0, False),
        )
        if now < cached_until:
            return cached_value
        ready = self.ready()
        self._profile_ready_cache[cache_key] = (now + (5 if ready else 1), ready)
        return ready

    # 方法说明：绑定输入后提交完整工作流并下载最后一个结果图。
    def run(
        self,
        workflow_template: dict,
        *,
        input_images: dict[str, bytes],
        output_prefix: str,
        prepare_workflow: WorkflowMutator | None = None,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        if prepare_workflow is not None:
            prepare_workflow(workflow)
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        ) as client:
            uploaded_by_digest: dict[str, str] = {}
            uploaded_inputs: dict[str, str] = {}
            for role, image_bytes in input_images.items():
                digest = hashlib.sha256(image_bytes).hexdigest()
                uploaded = uploaded_by_digest.get(digest)
                if uploaded is None:
                    uploaded = self._upload(client, image_bytes, role.lower())
                    uploaded_by_digest[digest] = uploaded
                uploaded_inputs[role] = uploaded
            output_nodes = bind_io(
                workflow,
                input_images=uploaded_inputs,
                output_prefix=output_prefix,
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

    # 方法说明：将图像字节规范化为 PNG 后上传到 ComfyUI。
    def _upload(
        self,
        client: httpx.Client,
        image_bytes: bytes,
        role: str,
    ) -> str:
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
        return comfy_path(upload.json())

    # 方法说明：轮询 ComfyUI 历史记录直到获得输出或超时。
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


# 方法说明：发现并绑定工作流的输入、参考图和输出节点。
def bind_io(
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


# 方法说明：拼接 ComfyUI 上传文件的内部路径。
def comfy_path(uploaded: dict) -> str:
    name = uploaded["name"]
    subfolder = uploaded.get("subfolder", "")
    return f"{subfolder}/{name}" if subfolder else name
