"""RPE mutation record persistence (B3a).

Raw, append-only persistence of applied ``RPEMutationRecord``s to aiosqlite — one
row per mutation. This is NOT aggregation: the per-(trace_id, target_key)
single-apply selection happens in ``RPEMutationService`` and is untouched here;
persistence is a pure side-effect after a record is finalized. It is telemetry
plus the durable basis for rollback (``previous_value`` survives restart for
manual rollback / audit; the automatic timeout scheduler is B4).

aiosqlite pattern mirrors ``ExactCache``: this store owns its DDL and lazy-inits
the table once, shares the app DB file, and uses one connection per operation
(no pool). ``applied_at`` is stored as the record's raw monotonic value (process
-relative telemetry — NOT a wall-clock); ``persisted_at`` is the wall-clock ISO
timestamp written here, which is the field to compare across restarts.

Isolation: imports only aiosqlite + app.core.errors + app.db.sqlite + RPE models.
No app.api / app.main / app.routing / app.memory / network / LLM.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.core.errors import DatabaseError
from app.db.sqlite import _normalize_path
from app.rpe.models import RPEMutationRecord

_DDL = """
CREATE TABLE IF NOT EXISTS rpe_mutation_records (
    rollback_id            TEXT PRIMARY KEY,
    trace_id               TEXT NOT NULL,
    session_id             TEXT,
    category               TEXT,
    difficulty             INTEGER,
    target                 TEXT NOT NULL,
    target_key             TEXT NOT NULL,
    previous_value         REAL NOT NULL,
    applied_delta          REAL NOT NULL,
    new_value              REAL NOT NULL,
    proposed_delta         REAL NOT NULL,
    confidence             REAL NOT NULL,
    applied_at             REAL NOT NULL,
    persisted_at           TEXT NOT NULL,
    expires_at             TEXT,
    rollback_status        TEXT NOT NULL,
    current_value_mismatch INTEGER NOT NULL
)
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_rpe_records_session "
    "ON rpe_mutation_records(session_id, category, difficulty)"
)


class RPERecordStore:
    """aiosqlite-backed raw store for applied RPE mutation records."""

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
                    await conn.execute(_INDEX_DDL)
                    await conn.commit()
            except sqlite3.Error as exc:
                raise DatabaseError(f"RPERecordStore init failed: {exc}") from exc
            self._initialized = True

    async def persist(self, record: RPEMutationRecord) -> None:
        """Insert one row for a finalized mutation record. rollback_id is the PK
        (uuid4, unique per applied mutation), so this is an append — never an
        aggregate."""
        await self._ensure_init()
        ctx = record.proposal.decision.context
        persisted_at = datetime.now(timezone.utc).isoformat()
        # expires_at stays NULL in B3a (record.expires_at is None until B4 wires
        # the timeout scheduler, which will store a wall-clock value separately).
        try:
            async with aiosqlite.connect(self._path) as conn:
                await conn.execute(
                    "INSERT INTO rpe_mutation_records "
                    "(rollback_id, trace_id, session_id, category, difficulty, "
                    "target, target_key, previous_value, applied_delta, new_value, "
                    "proposed_delta, confidence, applied_at, persisted_at, "
                    "expires_at, rollback_status, current_value_mismatch) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.rollback_id,
                        ctx.trace_id,
                        ctx.session_id,
                        ctx.category,
                        ctx.difficulty,
                        record.proposal.target,
                        record.proposal.target_key,
                        record.previous_value,
                        record.applied_delta,
                        record.new_value,
                        record.proposal.proposed_delta,
                        record.proposal.confidence,
                        record.applied_at,
                        persisted_at,
                        None,
                        record.rollback_status,
                        1 if record.current_value_mismatch else 0,
                    ),
                )
                await conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"RPERecordStore persist failed: {exc}") from exc

    async def update_status(self, rollback_id: str, rollback_status: str) -> None:
        """Update the rollback_status of a persisted record (no-op row if the
        rollback_id was never persisted)."""
        await self._ensure_init()
        try:
            async with aiosqlite.connect(self._path) as conn:
                await conn.execute(
                    "UPDATE rpe_mutation_records SET rollback_status = ? "
                    "WHERE rollback_id = ?",
                    (rollback_status, rollback_id),
                )
                await conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"RPERecordStore update_status failed: {exc}"
            ) from exc

    async def delete_older_than(
        self, now: float, max_age_s: float, batch_limit: int
    ) -> int:
        """B9 (GlymphaticCleaner) — prune records persisted before the cutoff.

        Satisfies the cleaner's ``AgeCleanableStore`` protocol structurally (no
        import from app.maintenance). ``persisted_at`` is the wall-clock ISO
        timestamp, which sorts lexicographically = chronologically, so a string
        ``<`` comparison is a correct age test. ``batch_limit`` bounds the delete
        (oldest first). Returns rows deleted. Errors surface as DatabaseError; the
        cleaner handles them fail-open.
        """
        await self._ensure_init()
        # Clamp to the epoch: an absurdly large max_age_s would push the cutoff
        # before 1970 and make datetime.fromtimestamp raise OSError. A pre-epoch
        # cutoff means "nothing is old enough" (keep everything) anyway.
        cutoff_epoch = max(0.0, now - max_age_s)
        cutoff_iso = datetime.fromtimestamp(
            cutoff_epoch, tz=timezone.utc
        ).isoformat()
        try:
            async with aiosqlite.connect(self._path) as conn:
                cur = await conn.execute(
                    "DELETE FROM rpe_mutation_records WHERE rollback_id IN ("
                    "SELECT rollback_id FROM rpe_mutation_records "
                    "WHERE persisted_at < ? ORDER BY persisted_at LIMIT ?)",
                    (cutoff_iso, batch_limit),
                )
                deleted = cur.rowcount
                await conn.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"RPERecordStore delete_older_than failed: {exc}"
            ) from exc
        return deleted if isinstance(deleted, int) and deleted > 0 else 0

    async def fetch(self, rollback_id: str) -> dict[str, Any] | None:
        await self._ensure_init()
        try:
            async with aiosqlite.connect(self._path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM rpe_mutation_records WHERE rollback_id = ?",
                    (rollback_id,),
                ) as cur:
                    row = await cur.fetchone()
                    return dict(row) if row is not None else None
        except sqlite3.Error as exc:
            raise DatabaseError(f"RPERecordStore fetch failed: {exc}") from exc

    async def fetch_all(self) -> list[dict[str, Any]]:
        await self._ensure_init()
        try:
            async with aiosqlite.connect(self._path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM rpe_mutation_records ORDER BY persisted_at"
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(f"RPERecordStore fetch_all failed: {exc}") from exc
