from __future__ import annotations

from io import BytesIO
import asyncio
import json
from pathlib import Path
import time
from unittest.mock import MagicMock

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


# 方法说明：验证任务状态查询结束后显式关闭 SQLite 连接。
def test_store_closes_connection_after_status_query(tmp_path: Path, monkeypatch):
    store = PregenerationStore(tmp_path / "pregeneration")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchall.return_value = []

    # 方法说明：返回可追踪关闭调用的测试连接。
    def connect():
        return connection

    monkeypatch.setattr(store, "_connect", connect)

    assert store.list_work("copy_manga:missing") == []
    connection.close.assert_called_once_with()


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


# 方法说明：验证原图缓存独立于处理档位，并拒绝内容损坏的缓存文件。
def test_store_resolves_valid_source_cache_and_rejects_corruption(tmp_path: Path):
    store = PregenerationStore(tmp_path / "pregeneration")
    store.enqueue(**enqueue_values(priority=100, page_index=0))

    source = store.resolve_source("copy_manga:work", "chapter-1", 0)

    assert source is not None
    assert source["media_type"] == "image/png"
    assert source["source_bytes"] == len(png_bytes())
    assert store.get_source(source["source_id"])["source_sha256"] == source["source_sha256"]

    Path(source["source_path"]).write_bytes(b"corrupted")
    assert store.resolve_source("copy_manga:work", "chapter-1", 0) is None


# 方法说明：验证原图缓存按最近访问时间淘汰未被任务占用的旧文件。
def test_source_cache_evicts_old_unprotected_entries(tmp_path: Path):
    first = png_bytes("white")
    second = png_bytes("black")
    third = png_bytes("red")
    store = PregenerationStore(
        tmp_path / "pregeneration",
        source_cache_max_bytes=len(first) + len(third),
    )
    store.persist_source(
        work_key="copy_manga:work",
        chapter_id="chapter-1",
        page_index=0,
        image_bytes=first,
    )
    store.persist_source(
        work_key="copy_manga:work",
        chapter_id="chapter-1",
        page_index=1,
        image_bytes=second,
    )
    assert store.resolve_source("copy_manga:work", "chapter-1", 0) is not None

    store.persist_source(
        work_key="copy_manga:work",
        chapter_id="chapter-1",
        page_index=2,
        image_bytes=third,
    )

    assert store.resolve_source("copy_manga:work", "chapter-1", 0) is not None
    assert store.resolve_source("copy_manga:work", "chapter-1", 1) is None
    assert store.resolve_source("copy_manga:work", "chapter-1", 2) is not None


# 方法说明：验证处理档位修订变化不会继续命中旧章节增强结果。
def test_resolve_completed_requires_current_mode_revision(tmp_path: Path):
    store = PregenerationStore(tmp_path / "pregeneration")
    cache = ResultCache(tmp_path / "results")
    queued = store.enqueue(**enqueue_values(priority=100), mode_revision="revision-v1")
    claimed = store.claim_next()
    assert claimed is not None
    output = cache.temporary_result_path("a" * 64)
    output.write_bytes(b"result")
    result_path = cache.commit_result("a" * 64, output)
    cache.save_metadata("a" * 64, {})
    store.complete(
        queued["job_id"],
        "a" * 64,
        result_path,
        "fast",
        "revision-v1",
    )

    assert (
        store.resolve_completed(
            "copy_manga:work",
            "chapter-1",
            0,
            enqueue_values(priority=100)["options_json"],
            cache,
            mode_revision="revision-v1",
        )
        is not None
    )
    assert (
        store.resolve_completed(
            "copy_manga:work",
            "chapter-1",
            0,
            enqueue_values(priority=100)["options_json"],
            cache,
            mode_revision="revision-v2",
        )
        is None
    )


# 方法说明：验证失败页 manifest 不声明不存在的章节结果文件。
def test_failed_page_manifest_keeps_result_path_empty(tmp_path: Path):
    store = PregenerationStore(tmp_path / "pregeneration")
    queued = store.enqueue(**enqueue_values(priority=100, page_index=0))
    claimed = store.claim_next()
    assert claimed is not None
    failed = store.fail(queued["job_id"], "test_error", max_attempts=1)

    manifest_path = next((tmp_path / "chapter-cache").rglob("manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert manifest["pages"][0]["status"] == "failed"
    assert manifest["pages"][0]["result_path"] == ""


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
            "work_json": '{"source":"copy_manga","source_work_id":"work","title":"测试漫画"}',
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
        resolved = client.post(
            "/v1/pregeneration/cache/resolve",
            headers=headers,
            data={
                "work_json": request["data"]["work_json"],
                "chapter_json": request["data"]["chapter_json"],
                "options_json": request["data"]["options_json"],
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["cached"] is True
        assert resolved.json()["cache_key"] == completed["cache_key"]
        source = client.post(
            "/v1/pregeneration/source/resolve",
            headers=headers,
            data={
                "work_json": request["data"]["work_json"],
                "chapter_json": request["data"]["chapter_json"],
                "page_index": "0",
            },
        )
        assert source.status_code == 200
        assert source.json()["media_type"] == "image/png"
        source_image = client.get(source.json()["source_url"], headers=headers)
        assert source_image.status_code == 200
        assert source_image.content == png_bytes()
        different_mode = client.post(
            "/v1/pregeneration/cache/resolve",
            headers=headers,
            data={
                "work_json": request["data"]["work_json"],
                "chapter_json": request["data"]["chapter_json"],
                "options_json": '{"mode":"quality","page_index":0}',
            },
        )
        assert different_mode.status_code == 404
        completed_again = client.post(
            "/v1/pregeneration/pages", headers=headers, **request
        )
        assert completed_again.status_code == 202
        assert completed_again.json()["job_id"] == completed["job_id"]
        assert completed_again.json()["status"] == "completed"

    manifests = list((runtime / "chapter-cache").rglob("manifest.json"))
    assert len(manifests) == 1
    assert manifests[0].parent.parts[-4:] == (
        "chapter-cache",
        "测试漫画",
        "第一话",
        "fast",
    )
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert [page["page_index"] for page in manifest["pages"]] == [0, 1]
    assert all(page["status"] == "completed" for page in manifest["pages"])
    assert (manifests[0].parent / "01.webp").is_file()
    assert (manifests[0].parent / "02.webp").is_file()


# 方法说明：验证普通可见页处理也会写入可供后续档位复用的原图缓存。
def test_visible_page_process_persists_source_cache(tmp_path: Path):
    settings = Settings(api_token="test-token", runtime_dir=tmp_path / "runtime")
    headers = {"Authorization": "Bearer test-token"}
    work_json = '{"source":"copy_manga","source_work_id":"work"}'
    chapter_json = '{"chapter_id":"chapter-visible","title":"可见页"}'

    with TestClient(create_app(settings)) as client:
        processed = client.post(
            "/v1/pages/process",
            headers=headers,
            data={
                "work_json": work_json,
                "chapter_json": chapter_json,
                "options_json": '{"mode":"fast","page_index":3}',
            },
            files={"image": ("page.png", png_bytes(), "image/png")},
        )
        source = client.post(
            "/v1/pregeneration/source/resolve",
            headers=headers,
            data={
                "work_json": work_json,
                "chapter_json": chapter_json,
                "page_index": "3",
            },
        )

    assert processed.status_code == 200
    assert source.status_code == 200
    assert source.json()["source_bytes"] == len(png_bytes())


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
