from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..domain import ProcessOptions, WorkIdentity
from ..logging_utils import exception_log_fields, log_operation
from .page_processing import process_page_with_references


logger = logging.getLogger(__name__)


@dataclass
class PregenerationService:
    """消费持久化章节任务并复用统一页面处理链。"""

    store: object
    cache: object
    processor: object
    metadata: object
    reference_bank: object
    identities: object
    settings: object
    _wake_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _worker_task: asyncio.Task | None = field(default=None, init=False)
    _stopping: bool = field(default=False, init=False)

    # 方法说明：恢复中断任务并启动单消费者后台循环。
    async def start(self) -> None:
        recovery = await asyncio.to_thread(self.store.recover, self.cache)
        self._stopping = False
        self._worker_task = asyncio.create_task(
            self._run(), name="comic-enhancer-pregeneration"
        )
        self._wake_event.set()
        log_operation(
            logger,
            logging.INFO,
            feature="预生成队列恢复",
            parameters={"database": str(self.store.database_path)},
            result={"status": "started", **recovery},
        )

    # 方法说明：等待当前任务收尾后停止后台消费者。
    async def stop(self) -> None:
        self._stopping = True
        self._wake_event.set()
        if self._worker_task is not None:
            await self._worker_task
            self._worker_task = None

    # 方法说明：持久化上传页并唤醒后台消费者。
    async def enqueue(self, **values) -> dict:
        job = await asyncio.to_thread(self.store.enqueue, **values)
        self._wake_event.set()
        return job

    # 方法说明：持续领取最高优先级任务，空闲时等待新任务通知。
    async def _run(self) -> None:
        while True:
            self._wake_event.clear()
            job = await asyncio.to_thread(self.store.claim_next)
            if job is None:
                if self._stopping:
                    return
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=5)
                except TimeoutError:
                    pass
                continue
            await self._process_job(job)
            if self._stopping:
                return
            await asyncio.sleep(0)

    # 方法说明：校验持久化原图并完成一次后台推理和章节缓存提交。
    async def _process_job(self, job: dict) -> None:
        started = time.perf_counter()
        parameters = {
            "job_id": job["job_id"],
            "work_key": job["work_key"],
            "chapter_id": job["chapter_id"],
            "chapter_title": job.get("chapter_title", ""),
            "page_index": job["page_index"],
            "page_number": int(job["page_index"]) + 1,
            "mode": job.get("mode", ""),
            "priority": job["priority"],
            "attempt": job["attempts"],
        }
        log_operation(
            logger,
            logging.INFO,
            feature="章节页面预生成开始",
            parameters=parameters,
            result={"status": "processing"},
        )
        try:
            source_path = Path(job["source_path"])
            image_bytes = await asyncio.to_thread(source_path.read_bytes)
            if hashlib.sha256(image_bytes).hexdigest() != job["source_sha256"]:
                raise ValueError("预生成原图哈希校验失败")
            work = self.identities.enrich(
                WorkIdentity.model_validate(job["work_json"])
            )
            options = ProcessOptions.model_validate(job["options_json"])
            if str(options.mode) == "flux2_character_lineart":
                options = options.model_copy(update={"comfyui_direct_output": False})
            mode_revision = await asyncio.to_thread(
                self.processor.base_cache_revision,
                options,
            )
            result = await process_page_with_references(
                processor=self.processor,
                metadata=self.metadata,
                reference_bank=self.reference_bank,
                settings=self.settings,
                image_bytes=image_bytes,
                work=work,
                options=options,
                priority=int(job["priority"]),
            )
            result_path = self.cache.result_path(result.cache_key)
            await asyncio.to_thread(
                self.store.complete,
                job["job_id"],
                result.cache_key,
                result_path,
                str(options.mode),
                mode_revision,
            )
            log_operation(
                logger,
                logging.INFO,
                feature="章节页面预生成",
                parameters=parameters,
                result={
                    "status": "completed",
                    "cache_key": result.cache_key[:12],
                    "cached": result.cached,
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as error:
            failed = await asyncio.to_thread(
                self.store.fail, job["job_id"], type(error).__name__
            )
            if failed["status"] == "queued":
                self._wake_event.set()
            log_operation(
                logger,
                logging.ERROR,
                feature="章节页面预生成",
                parameters=parameters,
                result={
                    "status": failed["status"],
                    **exception_log_fields(error),
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
