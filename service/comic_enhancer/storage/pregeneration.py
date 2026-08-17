from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class PregenerationStore:
    """持久化章节预生成任务、原图文件和章节 manifest。"""

    # 方法说明：初始化 SQLite 数据库、WAL 模式和运行时目录。
    def __init__(self, root: Path):
        self.root = root
        self.database_path = root / "jobs.sqlite3"
        self.source_root = root / "source"
        self.chapter_root = root.parent / "chapter-cache"
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.chapter_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # 方法说明：创建任务表和保证排序稳定的索引。
    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    work_key TEXT NOT NULL,
                    work_json TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    chapter_title TEXT NOT NULL,
                    page_index INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    cache_key TEXT NOT NULL DEFAULT '',
                    result_path TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                    ON jobs(status, priority, sequence);
                CREATE INDEX IF NOT EXISTS idx_jobs_work
                    ON jobs(work_key, chapter_id, options_json);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "mode" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'fast'"
                )

    # 方法说明：打开配置了超时和行字典访问的 SQLite 连接。
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    # 方法说明：将外部作品、章节标识转换为安全且稳定的目录片段。
    @staticmethod
    def _safe_component(value: str, fallback: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._")
        return (normalized[:80] or fallback) + "-" + hashlib.sha1(
            str(value).encode("utf-8")
        ).hexdigest()[:10]

    # 方法说明：返回指定作品、章节和档位的持久化缓存目录。
    def chapter_directory(self, work_key: str, chapter_id: str, mode: str) -> Path:
        path = self.chapter_root / self._safe_component(work_key, "work") / self._safe_component(
            chapter_id, "chapter"
        ) / self._safe_component(mode, "mode")
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 方法说明：原子写入章节缓存 manifest，避免重启读取半份 JSON。
    def _write_manifest(self, rows: list[sqlite3.Row], work_key: str, chapter_id: str, mode: str) -> None:
        directory = self.chapter_directory(work_key, chapter_id, mode)
        payload = {
            "version": 1,
            "work_key": work_key,
            "chapter_id": chapter_id,
            "mode": mode,
            "updated_at": time.time(),
            "pages": [
                {
                    "page_index": row["page_index"],
                    "page_count": row["page_count"],
                    "status": row["status"],
                    "cache_key": row["cache_key"],
                    "result_path": row["result_path"],
                    "error": row["error"],
                }
                for row in rows
            ],
        }
        temporary = directory / f".manifest.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(directory / "manifest.json")

    # 方法说明：把单页原图写入章节源文件目录并返回哈希和路径。
    def _persist_source(self, work_key: str, chapter_id: str, page_index: int, image_bytes: bytes) -> tuple[str, Path]:
        digest = hashlib.sha256(image_bytes).hexdigest()
        directory = self.source_root / self._safe_component(work_key, "work") / self._safe_component(
            chapter_id, "chapter"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"page-{page_index + 1:04d}-{digest[:16]}.img"
        valid_existing = False
        if path.is_file():
            try:
                valid_existing = hashlib.sha256(path.read_bytes()).hexdigest() == digest
            except OSError:
                valid_existing = False
        if not valid_existing:
            temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(image_bytes)
            temporary.replace(path)
        return digest, path

    # 方法说明：持久化一个可去重的章节页面任务并返回任务快照。
    def enqueue(
        self,
        *,
        work_key: str,
        work_json: dict[str, Any],
        chapter_id: str,
        chapter_title: str,
        page_index: int,
        page_count: int,
        options_json: dict[str, Any],
        priority: int,
        image_bytes: bytes,
    ) -> dict[str, Any]:
        source_sha256, source_path = self._persist_source(work_key, chapter_id, page_index, image_bytes)
        options_text = json.dumps(options_json, ensure_ascii=False, sort_keys=True)
        dedupe_key = hashlib.sha256(
            "|".join(
                [work_key, chapter_id, str(page_index), source_sha256, options_text]
            ).encode("utf-8")
        ).hexdigest()
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            if row is None:
                sequence = int(
                    connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM jobs").fetchone()[0]
                )
                job_id = uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO jobs (
                        job_id, dedupe_key, work_key, work_json, chapter_id, chapter_title,
                        page_index, page_count, source_path, source_sha256, options_json,
                        mode, priority, sequence, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                    (
                        job_id,
                        dedupe_key,
                        work_key,
                        json.dumps(work_json, ensure_ascii=False, sort_keys=True),
                        chapter_id,
                        chapter_title,
                        page_index,
                        page_count,
                        str(source_path),
                        source_sha256,
                        options_text,
                        str(options_json.get("mode", "fast")),
                        max(0, int(priority)),
                        sequence,
                        now,
                        now,
                    ),
                )
            else:
                job_id = row["job_id"]
                reset = row["status"] in {"failed", "completed"}
                connection.execute(
                    """UPDATE jobs SET
                        chapter_title=?, page_count=?, source_path=?, source_sha256=?,
                        options_json=?, mode=?, priority=?,
                        status=?, cache_key=?, result_path=?, error=?, completed_at=?, updated_at=?
                    WHERE job_id=?""",
                    (
                        chapter_title,
                        page_count,
                        str(source_path),
                        source_sha256,
                        options_text,
                        str(options_json.get("mode", "fast")),
                        max(0, int(priority)),
                        "queued" if reset else row["status"],
                        "" if reset else row["cache_key"],
                        "" if reset else row["result_path"],
                        "" if reset else row["error"],
                        None if reset else row["completed_at"],
                        now,
                        job_id,
                    ),
                )
            result = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            assert result is not None
            return self._row_to_dict(result)

    # 方法说明：启动时把上次进程中断的任务恢复为可重试状态。
    def recover(self, cache: Any) -> dict[str, int]:
        reset = 0
        repaired = 0
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM jobs").fetchall()
            for row in rows:
                source_exists = Path(row["source_path"]).is_file()
                if row["status"] == "processing" and source_exists:
                    connection.execute(
                        "UPDATE jobs SET status='queued', updated_at=? WHERE job_id=?",
                        (time.time(), row["job_id"]),
                    )
                    reset += 1
                elif not source_exists and row["status"] in {"queued", "processing", "completed"}:
                    connection.execute(
                        "UPDATE jobs SET status='failed', error='source_missing', updated_at=? WHERE job_id=?",
                        (time.time(), row["job_id"]),
                    )
                    repaired += 1
                elif row["status"] == "completed" and (
                    not row["cache_key"] or not cache.is_complete(row["cache_key"])
                ):
                    connection.execute(
                        "UPDATE jobs SET status='queued', cache_key='', result_path='', completed_at=NULL, updated_at=? WHERE job_id=?",
                        (time.time(), row["job_id"]),
                    )
                    repaired += 1
        return {"processing_reset": reset, "cache_requeued": repaired}

    # 方法说明：按优先级和入队序号领取一个任务并标记为处理中。
    def claim_next(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY priority ASC, sequence ASC LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            connection.execute(
                "UPDATE jobs SET status='processing', attempts=attempts+1, updated_at=? WHERE job_id=?",
                (time.time(), row["job_id"]),
            )
            connection.execute("COMMIT")
            return self._row_to_dict(connection.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())

    # 方法说明：将成功推理结果写入任务并刷新章节 manifest。
    def complete(self, job_id: str, cache_key: str, result_path: Path, mode: str) -> dict[str, Any]:
        with self._connect() as connection:
            now = time.time()
            connection.execute(
                "UPDATE jobs SET status='completed', cache_key=?, result_path=?, error='', completed_at=?, updated_at=? WHERE job_id=?",
                (cache_key, str(result_path), now, now, job_id),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            assert row is not None
            rows = connection.execute(
                "SELECT * FROM jobs WHERE work_key=? AND chapter_id=? AND mode=? ORDER BY page_index",
                (row["work_key"], row["chapter_id"], row["mode"]),
            ).fetchall()
        self._link_chapter_result(row, result_path, mode)
        self._write_manifest(rows, row["work_key"], row["chapter_id"], mode)
        return self._row_to_dict(row)

    # 方法说明：记录失败原因并按重试次数决定继续排队还是终止。
    def fail(self, job_id: str, error: str, max_attempts: int = 3) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            status = "queued" if int(row["attempts"]) < max_attempts else "failed"
            connection.execute(
                "UPDATE jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
                (status, error[:500], time.time(), job_id),
            )
            result = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            assert result is not None
            rows = connection.execute(
                "SELECT * FROM jobs WHERE work_key=? AND chapter_id=? AND mode=? ORDER BY page_index",
                (result["work_key"], result["chapter_id"], result["mode"]),
            ).fetchall()
        self._write_manifest(rows, result["work_key"], result["chapter_id"], result["mode"])
        return self._row_to_dict(result)

    # 方法说明：查询单个任务的持久化状态。
    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    # 方法说明：按作品查询章节预生成状态，供插件恢复和进度展示。
    def list_work(self, work_key: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE work_key=? ORDER BY chapter_id, page_index",
                (work_key,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    # 方法说明：将结果链接到章节档位目录，失败时退化为复制。
    def _link_chapter_result(self, row: sqlite3.Row, result_path: Path, mode: str) -> None:
        directory = self.chapter_directory(row["work_key"], row["chapter_id"], mode)
        target = directory / f"page-{int(row['page_index']) + 1:04d}-{row['cache_key'][:16]}.webp"
        if target.exists():
            return
        try:
            os.link(result_path, target)
        except OSError:
            shutil.copy2(result_path, target)

    # 方法说明：把 SQLite 行转换成不含内部连接对象的任务字典。
    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise KeyError("任务不存在")
        value = dict(row)
        value["work_json"] = json.loads(value["work_json"])
        value["options_json"] = json.loads(value["options_json"])
        return value
