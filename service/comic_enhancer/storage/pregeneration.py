from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
    def __init__(
        self,
        root: Path,
        *,
        source_cache_max_bytes: int = 20 * 1024 * 1024 * 1024,
    ):
        self.root = root
        self.database_path = root / "jobs.sqlite3"
        self.source_root = root / "source"
        self.chapter_root = root.parent / "chapter-cache"
        self.source_cache_max_bytes = max(0, int(source_cache_max_bytes))
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.chapter_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # 方法说明：创建任务表和保证排序稳定的索引。
    def _initialize(self) -> None:
        with self._connection() as connection:
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
                    mode_revision TEXT NOT NULL DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS source_cache (
                    source_id TEXT PRIMARY KEY,
                    work_key TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    page_index INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    UNIQUE(work_key, chapter_id, page_index)
                );
                CREATE INDEX IF NOT EXISTS idx_source_cache_page
                    ON source_cache(work_key, chapter_id, page_index);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "mode" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'fast'"
                )
            if "mode_revision" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN mode_revision TEXT NOT NULL DEFAULT ''"
                )
        self._backfill_source_cache()

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

    # 方法说明：提供保留事务语义且退出时必定关闭的 SQLite 连接。
    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # 方法说明：保留中文标题并移除文件系统不允许的字符。
    @staticmethod
    def _display_component(value: str, fallback: str) -> str:
        normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value)).strip(" .")
        if normalized in {"", ".", ".."}:
            normalized = fallback
        return normalized[:120]

    # 方法说明：从任务行提取可读的作品标题和章节标题。
    @classmethod
    def _row_display_names(cls, row: sqlite3.Row) -> tuple[str, str]:
        try:
            work = json.loads(row["work_json"])
        except (json.JSONDecodeError, TypeError):
            work = {}
        work_title = str(work.get("title") or row["work_key"])
        chapter_title = str(row["chapter_title"] or row["chapter_id"])
        return (
            cls._display_component(work_title, "未命名作品"),
            cls._display_component(chapter_title, str(row["chapter_id"])),
        )

    # 方法说明：返回指定作品、章节和档位的持久化缓存目录。
    def chapter_directory(
        self,
        work_key: str,
        chapter_id: str,
        mode: str,
        work_title: str = "",
        chapter_title: str = "",
    ) -> Path:
        path = self.chapter_root / self._display_component(
            work_title or work_key, "未命名作品"
        ) / self._display_component(chapter_title or chapter_id, chapter_id) / self._display_component(
            mode, "mode"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 方法说明：原子写入章节缓存 manifest，避免重启读取半份 JSON。
    def _write_manifest(
        self,
        rows: list[sqlite3.Row],
        work_key: str,
        chapter_id: str,
        mode: str,
    ) -> None:
        work_title, chapter_title = self._row_display_names(rows[0]) if rows else (
            work_key,
            chapter_id,
        )
        directory = self.chapter_directory(
            work_key, chapter_id, mode, work_title, chapter_title
        )
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
                    "result_path": (
                        f"{int(row['page_index']) + 1:02d}.webp"
                        if row["status"] == "completed" and row["cache_key"]
                        else ""
                    ),
                    "error": row["error"],
                }
                for row in rows
            ],
        }
        temporary = directory / f".manifest.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(directory / "manifest.json")

    # 方法说明：把单页原图写入章节源文件目录并返回哈希和路径。
    def _persist_source(
        self,
        work_key: str,
        chapter_id: str,
        page_index: int,
        image_bytes: bytes,
        work_title: str = "",
        chapter_title: str = "",
    ) -> tuple[str, Path]:
        digest = hashlib.sha256(image_bytes).hexdigest()
        directory = self.source_root / self._display_component(
            work_title or work_key, "未命名作品"
        ) / self._display_component(chapter_title or chapter_id, chapter_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{page_index + 1:02d}-{digest[:16]}.img"
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

    # 方法说明：生成不暴露文件系统路径的稳定原图标识。
    @staticmethod
    def _source_id(work_key: str, chapter_id: str, page_index: int) -> str:
        payload = f"{work_key}|{chapter_id}|{int(page_index)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # 方法说明：根据图片文件头返回浏览器可直接显示的媒体类型。
    @staticmethod
    def _image_media_type(header: bytes) -> str:
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "image/webp"
        if b"ftypavif" in header[:32]:
            return "image/avif"
        return "application/octet-stream"

    # 方法说明：记录单页原图并更新独立于处理档位的缓存索引。
    def persist_source(
        self,
        *,
        work_key: str,
        chapter_id: str,
        page_index: int,
        image_bytes: bytes,
        work_title: str = "",
        chapter_title: str = "",
    ) -> dict[str, Any]:
        source_sha256, source_path = self._persist_source(
            work_key,
            chapter_id,
            page_index,
            image_bytes,
            work_title,
            chapter_title,
        )
        source_id = self._source_id(work_key, chapter_id, page_index)
        now = time.time()
        media_type = self._image_media_type(image_bytes[:32])
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO source_cache (
                    source_id, work_key, chapter_id, page_index, source_path,
                    source_sha256, source_bytes, media_type, created_at,
                    updated_at, accessed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_key, chapter_id, page_index) DO UPDATE SET
                    source_path=excluded.source_path,
                    source_sha256=excluded.source_sha256,
                    source_bytes=excluded.source_bytes,
                    media_type=excluded.media_type,
                    updated_at=excluded.updated_at,
                    accessed_at=excluded.accessed_at""",
                (
                    source_id,
                    work_key,
                    chapter_id,
                    int(page_index),
                    str(source_path),
                    source_sha256,
                    len(image_bytes),
                    media_type,
                    now,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM source_cache WHERE source_id=?", (source_id,)
            ).fetchone()
        result = dict(row) if row is not None else {}
        result.update(self._prune_source_cache(protected_source_id=source_id))
        return result

    # 方法说明：按最近访问时间清理超限原图且不触碰正在执行的任务。
    def _prune_source_cache(self, *, protected_source_id: str = "") -> dict[str, int]:
        if self.source_cache_max_bytes <= 0:
            return {"evicted_files": 0, "evicted_bytes": 0}
        evicted_files = 0
        evicted_bytes = 0
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM source_cache ORDER BY accessed_at ASC"
            ).fetchall()
            total = sum(int(row["source_bytes"]) for row in rows)
            for row in rows:
                if total <= self.source_cache_max_bytes:
                    break
                if row["source_id"] == protected_source_id:
                    continue
                active = connection.execute(
                    """SELECT 1 FROM jobs
                    WHERE source_path=? AND status IN ('queued', 'processing')
                    LIMIT 1""",
                    (row["source_path"],),
                ).fetchone()
                if active is not None:
                    continue
                path = Path(row["source_path"])
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                connection.execute(
                    "DELETE FROM source_cache WHERE source_id=?",
                    (row["source_id"],),
                )
                size = int(row["source_bytes"])
                total -= size
                evicted_files += 1
                evicted_bytes += size
        return {"evicted_files": evicted_files, "evicted_bytes": evicted_bytes}

    # 方法说明：从历史预生成任务回填独立原图索引以复用已有文件。
    def _backfill_source_cache(self) -> None:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM jobs
                WHERE source_path != ''
                ORDER BY updated_at ASC"""
            ).fetchall()
            now = time.time()
            for row in rows:
                path = Path(row["source_path"])
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                    with path.open("rb") as source:
                        media_type = self._image_media_type(source.read(32))
                except OSError:
                    continue
                source_id = self._source_id(
                    row["work_key"], row["chapter_id"], row["page_index"]
                )
                connection.execute(
                    """INSERT INTO source_cache (
                        source_id, work_key, chapter_id, page_index, source_path,
                        source_sha256, source_bytes, media_type, created_at,
                        updated_at, accessed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(work_key, chapter_id, page_index) DO NOTHING""",
                    (
                        source_id,
                        row["work_key"],
                        row["chapter_id"],
                        int(row["page_index"]),
                        str(path),
                        row["source_sha256"],
                        size,
                        media_type,
                        float(row["created_at"] or now),
                        float(row["updated_at"] or now),
                        now,
                    ),
                )

    # 方法说明：按作品、章节和页码返回校验通过的本地原图缓存。
    def resolve_source(
        self,
        work_key: str,
        chapter_id: str,
        page_index: int,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM source_cache
                WHERE work_key=? AND chapter_id=? AND page_index=?""",
                (work_key, chapter_id, int(page_index)),
            ).fetchone()
            if row is None or not self._valid_source_row(row):
                return None
            connection.execute(
                "UPDATE source_cache SET accessed_at=? WHERE source_id=?",
                (time.time(), row["source_id"]),
            )
            return dict(row)

    # 方法说明：按不透明原图标识返回校验通过的缓存文件记录。
    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_cache WHERE source_id=?", (source_id,)
            ).fetchone()
            if row is None or not self._valid_source_row(row):
                return None
            connection.execute(
                "UPDATE source_cache SET accessed_at=? WHERE source_id=?",
                (time.time(), source_id),
            )
            return dict(row)

    # 方法说明：校验原图文件存在、大小一致且内容哈希未损坏。
    @staticmethod
    def _valid_source_row(row: sqlite3.Row) -> bool:
        path = Path(row["source_path"])
        try:
            if not path.is_file() or path.stat().st_size != int(row["source_bytes"]):
                return False
            return hashlib.sha256(path.read_bytes()).hexdigest() == row["source_sha256"]
        except OSError:
            return False

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
        mode_revision: str = "",
    ) -> dict[str, Any]:
        work_title = str(work_json.get("title") or work_key)
        source = self.persist_source(
            work_key=work_key,
            chapter_id=chapter_id,
            page_index=page_index,
            image_bytes=image_bytes,
            work_title=work_title,
            chapter_title=chapter_title,
        )
        source_sha256 = str(source["source_sha256"])
        source_path = Path(str(source["source_path"]))
        options_text = json.dumps(options_json, ensure_ascii=False, sort_keys=True)
        dedupe_key = hashlib.sha256(
            "|".join(
                [work_key, chapter_id, str(page_index), source_sha256, options_text]
            ).encode("utf-8")
        ).hexdigest()
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
                        mode, mode_revision, priority, sequence, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
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
                        mode_revision,
                        max(0, int(priority)),
                        sequence,
                        now,
                        now,
                    ),
                )
            else:
                job_id = row["job_id"]
                completed_result_missing = (
                    row["status"] == "completed"
                    and not Path(row["result_path"]).is_file()
                )
                revision_changed = row["mode_revision"] != mode_revision
                reset = (
                    row["status"] == "failed"
                    or completed_result_missing
                    or (revision_changed and row["status"] != "processing")
                )
                stored_revision = (
                    row["mode_revision"] if row["status"] == "processing" else mode_revision
                )
                connection.execute(
                    """UPDATE jobs SET
                        chapter_title=?, page_count=?, source_path=?, source_sha256=?,
                        options_json=?, mode=?, mode_revision=?, priority=?,
                        status=?, cache_key=?, result_path=?, error=?, completed_at=?, updated_at=?
                    WHERE job_id=?""",
                    (
                        chapter_title,
                        page_count,
                        str(source_path),
                        source_sha256,
                        options_text,
                        str(options_json.get("mode", "fast")),
                        stored_revision,
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
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM jobs").fetchall()
            for row in rows:
                source_exists = Path(row["source_path"]).is_file()
                if row["status"] == "processing" and source_exists:
                    connection.execute(
                        "UPDATE jobs SET status='queued', updated_at=? WHERE job_id=?",
                        (time.time(), row["job_id"]),
                    )
                    reset += 1
                elif not source_exists and row["status"] in {"queued", "processing"}:
                    connection.execute(
                        "UPDATE jobs SET status='failed', error='source_missing', updated_at=? WHERE job_id=?",
                        (time.time(), row["job_id"]),
                    )
                    repaired += 1
                elif row["status"] == "completed" and (
                    not row["cache_key"] or not cache.is_complete(row["cache_key"])
                ):
                    if source_exists:
                        connection.execute(
                            "UPDATE jobs SET status='queued', cache_key='', result_path='', completed_at=NULL, updated_at=? WHERE job_id=?",
                            (time.time(), row["job_id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE jobs SET status='failed', cache_key='', result_path='', error='source_missing', completed_at=NULL, updated_at=? WHERE job_id=?",
                            (time.time(), row["job_id"]),
                        )
                    repaired += 1
        completed = []
        with self._connection() as connection:
            completed = connection.execute(
                "SELECT * FROM jobs WHERE status='completed' ORDER BY work_key, chapter_id, mode, page_index"
            ).fetchall()
        groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        for row in completed:
            groups.setdefault((row["work_key"], row["chapter_id"], row["mode"]), []).append(row)
        for (work_key, chapter_id, mode), group in groups.items():
            for row in group:
                if row["cache_key"] and cache.is_complete(row["cache_key"]):
                    self._link_chapter_result(row, cache.result_path(row["cache_key"]), mode)
            # 先确保所有结果文件已落盘，再原子发布 manifest，避免恢复中途出现半份章节缓存。
            self._write_manifest(group, work_key, chapter_id, mode)
        return {"processing_reset": reset, "cache_requeued": repaired}

    # 方法说明：按作品、章节、页码和完整处理选项查询已完成缓存。
    def resolve_completed(
        self,
        work_key: str,
        chapter_id: str,
        page_index: int,
        options_json: dict[str, Any],
        cache: Any,
        mode_revision: str = "",
    ) -> dict[str, Any] | None:
        options_text = json.dumps(options_json, ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM jobs
                WHERE work_key=? AND chapter_id=? AND page_index=?
                  AND options_json=? AND mode_revision=? AND status='completed'
                ORDER BY completed_at DESC LIMIT 1""",
                (work_key, chapter_id, page_index, options_text, mode_revision),
            ).fetchone()
            if row is None:
                return None
            if not row["cache_key"] or not cache.is_complete(row["cache_key"]):
                source_exists = Path(row["source_path"]).is_file()
                connection.execute(
                    """UPDATE jobs SET status=?, cache_key='', result_path='',
                    error=?, completed_at=NULL, updated_at=? WHERE job_id=?""",
                    (
                        "queued" if source_exists else "failed",
                        "" if source_exists else "source_missing",
                        time.time(),
                        row["job_id"],
                    ),
                )
                return None
            return self._row_to_dict(row)

    # 方法说明：按优先级和入队序号领取一个任务并标记为处理中。
    def claim_next(self) -> dict[str, Any] | None:
        with self._connection() as connection:
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
    def complete(
        self,
        job_id: str,
        cache_key: str,
        result_path: Path,
        mode: str,
        mode_revision: str = "",
    ) -> dict[str, Any]:
        with self._connection() as connection:
            now = time.time()
            connection.execute(
                "UPDATE jobs SET status='completed', cache_key=?, mode_revision=?, result_path=?, error='', completed_at=?, updated_at=? WHERE job_id=?",
                (cache_key, mode_revision, str(result_path), now, now, job_id),
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    # 方法说明：按作品查询章节预生成状态，供插件恢复和进度展示。
    def list_work(self, work_key: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE work_key=? ORDER BY chapter_id, page_index",
                (work_key,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    # 方法说明：将结果链接到章节档位目录，失败时退化为复制。
    def _link_chapter_result(self, row: sqlite3.Row, result_path: Path, mode: str) -> None:
        work_title, chapter_title = self._row_display_names(row)
        directory = self.chapter_directory(
            row["work_key"], row["chapter_id"], mode, work_title, chapter_title
        )
        target = directory / f"{int(row['page_index']) + 1:02d}.webp"
        temporary = directory / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            os.link(result_path, temporary)
        except OSError:
            shutil.copy2(result_path, temporary)
        temporary.replace(target)

    # 方法说明：把 SQLite 行转换成不含内部连接对象的任务字典。
    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise KeyError("任务不存在")
        value = dict(row)
        value["work_json"] = json.loads(value["work_json"])
        value["options_json"] = json.loads(value["options_json"])
        return value
