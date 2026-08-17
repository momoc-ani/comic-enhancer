from __future__ import annotations

from io import BytesIO
import asyncio
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.config import Settings
from comic_enhancer.main import create_app
from comic_enhancer.application import PriorityInferenceGate
from comic_enhancer.storage import PregenerationStore, ResultCache


# 方法说明：生成预生成接口测试使用的最小 PNG 图片。
def png_bytes(color: str = "white") -> bytes:
    stream = BytesIO()
    Image.new("RGB", (32, 48), color).save(stream, format="PNG")
    return stream.getvalue()


# 方法说明：构造存储层入队需要的稳定测试参数。
def enqueue_values(priority: int, page_index: int = 0) -> dict:
    return {
        "work_key": "copy_manga:work",
        "work_json": {"source": "copy_manga", "source_work_id": "work"},
        "chapter_id": "chapter-1",
        "chapter_title": "第一话",
        "page_index": page_index,
        "page_count": 2,
        "options_json": {"mode": "fast", "page_index": page_index},
        "priority": priority,
        "image_bytes": png_bytes("white" if page_index == 0 else "black"),
    }


# 方法说明：验证 SQLite 队列按优先级领取并在重启时恢复处理中任务。
def test_store_prioritizes_and_recovers_processing_jobs(tmp_path: Path):
    store = PregenerationStore(tmp_path / "pregeneration")
    later = store.enqueue(**enqueue_values(priority=200, page_index=1))
    current = store.enqueue(**enqueue_values(priority=100, page_index=0))

    claimed = store.claim_next()
    assert claimed is not None
    assert claimed["job_id"] == current["job_id"]
    assert store.get(later["job_id"])["status"] == "queued"

    recovery = PregenerationStore(tmp_path / "pregeneration").recover(
        ResultCache(tmp_path / "results")
    )
    assert recovery["processing_reset"] == 1
    assert store.get(current["job_id"])["status"] == "queued"


# 方法说明：验证可见页优先级会在 GPU 空闲时抢在后台预生成任务之前。
def test_priority_inference_gate_prefers_visible_page():
    async def scenario():
        gate = PriorityInferenceGate(1)
        release = asyncio.Event()
        started: list[str] = []

        async def operation(name: str):
            started.append(name)
            if name == "background":
                await release.wait()
            return name

        background = asyncio.create_task(gate.run(100, lambda: operation("background")))
        while started != ["background"]:
            await asyncio.sleep(0)
        queued_background = asyncio.create_task(
            gate.run(200, lambda: operation("later"))
        )
        visible = asyncio.create_task(gate.run(0, lambda: operation("visible")))
        release.set()
        await asyncio.gather(background, visible, queued_background)
        return started

    assert asyncio.run(scenario()) == ["background", "visible", "later"]


# 方法说明：等待后台任务进入终态并返回最新任务状态。
def wait_for_job(client: TestClient, job_id: str, headers: dict[str, str]) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/v1/pregeneration/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("预生成任务未在测试时限内完成")


# 方法说明：验证异步接口鉴权、任务去重、章节缓存和 manifest 落盘。
def test_pregeneration_api_persists_and_completes_job(tmp_path: Path):
    runtime = tmp_path / "runtime"
    settings = Settings(api_token="test-token", runtime_dir=runtime)
    headers = {"Authorization": "Bearer test-token"}
    request = {
        "data": {
            "work_json": '{"source":"copy_manga","source_work_id":"work"}',
            "chapter_json": '{"chapter_id":"chapter-1","title":"第一话"}',
            "options_json": '{"mode":"fast","page_index":0}',
            "page_count": "1",
            "priority": "100",
        },
        "files": {"image": ("page.png", png_bytes(), "image/png")},
    }

    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/pregeneration/pages", **request).status_code == 401
        first = client.post(
            "/v1/pregeneration/pages", headers=headers, **request
        )
        second = client.post(
            "/v1/pregeneration/pages", headers=headers, **request
        )
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        second_page = client.post(
            "/v1/pregeneration/pages",
            headers=headers,
            data={
                **request["data"],
                "options_json": '{"mode":"fast","page_index":1}',
                "page_count": "2",
            },
            files={"image": ("page-2.png", png_bytes("black"), "image/png")},
        )
        assert second_page.status_code == 202
        completed = wait_for_job(client, first.json()["job_id"], headers)
        second_completed = wait_for_job(
            client, second_page.json()["job_id"], headers
        )
        assert completed["status"] == "completed"
        assert second_completed["status"] == "completed"
        assert completed["result_url"].endswith(".webp")
        assert client.get(completed["result_url"], headers=headers).status_code == 200

    manifests = list((runtime / "chapter-cache").rglob("manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert [page["page_index"] for page in manifest["pages"]] == [0, 1]
    assert all(page["status"] == "completed" for page in manifest["pages"])


# 方法说明：验证结果缓存和提交元数据在服务重启后仍直接命中。
def test_result_cache_survives_service_restart(tmp_path: Path):
    runtime = tmp_path / "runtime"
    settings = Settings(api_token="test-token", runtime_dir=runtime)
    headers = {"Authorization": "Bearer test-token"}
    request = {
        "headers": headers,
        "data": {
            "work_json": '{"source":"copy_manga","source_work_id":"work"}',
            "options_json": '{"mode":"fast","page_index":0}',
        },
        "files": {"image": ("page.png", png_bytes(), "image/png")},
    }

    with TestClient(create_app(settings)) as first_client:
        first = first_client.post("/v1/pages/process", **request)
    with TestClient(create_app(settings)) as second_client:
        second = second_client.post("/v1/pages/process", **request)

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert first.json()["cache_key"] == second.json()["cache_key"]
