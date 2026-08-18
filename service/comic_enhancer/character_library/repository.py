from __future__ import annotations

from array import array
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from ..character_vision import CharacterPageAnalysis
from .color_sampler import normalize_reference_image
from .models import CharacterProfile, CharacterReferenceAsset


class CharacterLibraryRepository:
    """使用 SQLite 和内容寻址文件保存角色库运行数据。"""

    # 方法说明：初始化角色库目录和数据库结构。
    def __init__(self, root: Path):
        self.root = root
        self.image_root = root / "images"
        self.database_path = root / "character-library.sqlite3"
        self.image_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # 方法说明：创建角色档案、页面计划和向量表。
    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS character_profiles (
                    cache_key TEXT PRIMARY KEY,
                    work_key TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    reference_sha256 TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_character_profiles_work
                    ON character_profiles(work_key, character_id);
                CREATE TABLE IF NOT EXISTS page_plans (
                    cache_key TEXT PRIMARY KEY,
                    work_key TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS character_embeddings (
                    work_key TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    reference_sha256 TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    PRIMARY KEY (work_key, character_id, reference_sha256, revision)
                );
                """
            )

    # 方法说明：创建带行对象支持的独立 SQLite 连接。
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
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

    # 方法说明：保存规范化参考图并返回其内容摘要。
    def store_reference(self, reference: CharacterReferenceAsset) -> str:
        normalized = normalize_reference_image(reference.image_bytes)
        digest = hashlib.sha256(normalized).hexdigest()
        path = self.image_root / digest[:2] / f"{digest}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(normalized)
            temporary.replace(path)
        return digest

    # 方法说明：读取指定键的缓存角色档案。
    def load_profile(self, cache_key: str) -> CharacterProfile | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT profile_json FROM character_profiles WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return CharacterProfile.model_validate_json(row["profile_json"]) if row else None

    # 方法说明：原子写入角色档案缓存。
    def save_profile(self, cache_key: str, profile: CharacterProfile) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO character_profiles
                (cache_key, work_key, character_id, reference_sha256, profile_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    profile.work_key,
                    profile.character_id,
                    profile.reference_sha256,
                    profile.model_dump_json(),
                    time.time(),
                ),
            )

    # 方法说明：读取指定键的缓存页面角色计划。
    def load_page_plan(self, cache_key: str) -> CharacterPageAnalysis | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT plan_json FROM page_plans WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        return CharacterPageAnalysis.model_validate_json(row["plan_json"]) if row else None

    # 方法说明：原子写入页面角色计划缓存。
    def save_page_plan(
        self,
        cache_key: str,
        work_key: str,
        plan: CharacterPageAnalysis,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO page_plans
                (cache_key, work_key, plan_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (cache_key, work_key, plan.model_dump_json(), time.time()),
            )

    # 方法说明：保存角色参考视图的轻量检索向量。
    def save_embedding(
        self,
        *,
        work_key: str,
        character_id: str,
        reference_sha256: str,
        revision: str,
        vector: tuple[float, ...],
    ) -> None:
        payload = array("f", vector).tobytes()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO character_embeddings
                (work_key, character_id, reference_sha256, revision, dimensions, vector)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    work_key,
                    character_id,
                    reference_sha256,
                    revision,
                    len(vector),
                    payload,
                ),
            )

    # 方法说明：读取当前作品的角色参考视图向量。
    def load_embeddings(
        self,
        work_key: str,
        revision: str,
    ) -> list[tuple[str, str, tuple[float, ...]]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT character_id, reference_sha256, dimensions, vector
                FROM character_embeddings
                WHERE work_key = ? AND revision = ?
                """,
                (work_key, revision),
            ).fetchall()
        results = []
        for row in rows:
            vector = array("f")
            vector.frombytes(row["vector"])
            if len(vector) == row["dimensions"]:
                results.append(
                    (row["character_id"], row["reference_sha256"], tuple(vector))
                )
        return results


# 方法说明：生成角色档案缓存键。
def profile_cache_key(
    *,
    work_key: str,
    character_id: str,
    reference_sha256: str,
    model_revision: str,
    template_revision: str,
) -> str:
    return _digest(
        [
            work_key,
            character_id,
            reference_sha256,
            model_revision,
            template_revision,
        ]
    )


# 方法说明：生成页面角色分析缓存键。
def page_plan_cache_key(
    *,
    work_key: str,
    image_bytes: bytes,
    profile_digests: list[str],
    model_revision: str,
    template_revision: str,
) -> str:
    return _digest(
        [
            work_key,
            hashlib.sha256(image_bytes).hexdigest(),
            *profile_digests,
            model_revision,
            template_revision,
        ]
    )


# 方法说明：将有序缓存键字段编码为 SHA-256。
def _digest(parts: list[str]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()
