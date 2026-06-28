"""Tier-1 Exact Cache (SQLite, sub-10ms)."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import datetime, timezone

import aiosqlite

from app.core.errors import DatabaseError
from app.db.sqlite import _normalize_path
from app.ingress.cache_key import exact_key, resolve_write_namespace

_DDL = """
CREATE TABLE IF NOT EXISTS exact_cache (
    prompt_hash TEXT PRIMARY KEY,
    prompt      TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    hit_count   INTEGER NOT NULL DEFAULT 0
)
"""


def _hash(prompt: str) -> str:
    """Legacy v1 키(프롬프트 해시 단독). 더 이상 get/put 에 쓰이지 않으며,
    네임스페이스 스킴(cache_key.exact_key) 도입 전 영속 row 를 식별하기 위한
    참조로만 보존한다(구 row graceful-miss 회귀 테스트 등). 신규 키는
    prompt_hash 컬럼에 exact_key() 결과(네임스페이스 포함)를 저장한다."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class ExactCache:
    """Hash-keyed exact-match cache backed by aiosqlite."""

    def __init__(self, database_url: str) -> None:
        self._path = _normalize_path(database_url)
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                async with aiosqlite.connect(self._path) as conn:
                    await conn.execute(_DDL)
                    await conn.commit()
            except sqlite3.Error as exc:
                raise DatabaseError(f"ExactCache init failed: {exc}") from exc
            self._initialized = True

    async def get(
        self,
        prompt: str,
        *,
        llm_mode: str = "mock",
        slot_fingerprint: str | None = None,
        model_id: str | None = None,
    ) -> str | None:
        await self._ensure_init()
        # 네임스페이스 포함 키. read 는 정책 강제 없이 그대로 조회한다 — live +
        # unresolved 조회는 허용되며 단지 resolved live 엔트리와 매칭되지 않아
        # miss 가 된다(read-side hardening). 구 스킴(prompt 단독 해시) row 는
        # 키가 달라 자연히 miss 이며 예외 없이 graceful 하다.
        key = exact_key(
            prompt,
            llm_mode=llm_mode,
            slot_fingerprint=slot_fingerprint,
            model_id=model_id,
        )
        try:
            async with aiosqlite.connect(self._path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT response FROM exact_cache WHERE prompt_hash = ?",
                    (key,),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    return None
                await conn.execute(
                    "UPDATE exact_cache SET hit_count = hit_count + 1 WHERE prompt_hash = ?",
                    (key,),
                )
                await conn.commit()
                return row["response"]
        except sqlite3.Error as exc:
            raise DatabaseError(f"ExactCache get failed: {exc}") from exc

    async def put(
        self,
        prompt: str,
        response: str,
        *,
        llm_mode: str = "mock",
        slot_fingerprint: str | None = None,
        model_id: str | None = None,
    ) -> None:
        await self._ensure_init()
        # write 는 네임스페이스를 확정·정책 강제한다 — 비-mock(live) write 는
        # resolved slot_fingerprint·model_id 가 없으면 CachePolicyError 로 거부한다
        # (unresolved 네임스페이스에 live 답변을 영속화하지 않는다).
        mode, fp, mid = resolve_write_namespace(
            llm_mode=llm_mode,
            slot_fingerprint=slot_fingerprint,
            model_id=model_id,
        )
        key = exact_key(prompt, llm_mode=mode, slot_fingerprint=fp, model_id=mid)
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with aiosqlite.connect(self._path) as conn:
                # INSERT OR REPLACE preserves uniqueness on prompt_hash; we
                # reset hit_count to 0 on fresh writes so it tracks real hits.
                await conn.execute(
                    "INSERT OR REPLACE INTO exact_cache "
                    "(prompt_hash, prompt, response, created_at, hit_count) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (key, prompt, response, now),
                )
                await conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"ExactCache put failed: {exc}") from exc
