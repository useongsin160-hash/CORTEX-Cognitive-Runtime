"""B9 — RPERecordStore.delete_older_than (persisted_at age prune).

Rows are inserted directly with controlled persisted_at values so the prune is
tested without building a full RPEMutationRecord. ISO timestamps sort
lexicographically = chronologically, so the SQL ``<`` comparison is a correct
age test.
"""
from __future__ import annotations

import time

import aiosqlite
import pytest

from app.rpe.record_store import RPERecordStore

_OLD = "2020-01-01T00:00:00+00:00"
_NEW = "2099-01-01T00:00:00+00:00"


async def _insert(store: RPERecordStore, rollback_id: str, persisted_at: str) -> None:
    async with aiosqlite.connect(store._path) as conn:
        await conn.execute(
            "INSERT INTO rpe_mutation_records (rollback_id, trace_id, target, "
            "target_key, previous_value, applied_delta, new_value, proposed_delta, "
            "confidence, applied_at, persisted_at, rollback_status, "
            "current_value_mismatch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rollback_id, "t", "synapse_weight", "category:coding",
                0.5, 0.1, 0.6, 0.1, 0.9, 0.0, persisted_at, "available", 0,
            ),
        )
        await conn.commit()


@pytest.fixture
async def store(tmp_path):
    s = RPERecordStore(str(tmp_path / "records.db"))
    await s._ensure_init()  # create the table before direct inserts
    return s


@pytest.mark.asyncio
async def test_prune_deletes_old_keeps_new(store):
    await _insert(store, "old", _OLD)
    await _insert(store, "new", _NEW)
    deleted = await store.delete_older_than(
        now=time.time(), max_age_s=86400.0, batch_limit=100
    )
    assert deleted == 1
    rows = await store.fetch_all()
    assert [r["rollback_id"] for r in rows] == ["new"]


@pytest.mark.asyncio
async def test_prune_batch_limit(store):
    for i in range(5):
        await _insert(store, f"old{i}", _OLD)
    deleted = await store.delete_older_than(
        now=time.time(), max_age_s=86400.0, batch_limit=2
    )
    assert deleted == 2
    rows = await store.fetch_all()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_prune_empty_returns_zero(store):
    deleted = await store.delete_older_than(
        now=time.time(), max_age_s=86400.0, batch_limit=100
    )
    assert deleted == 0


@pytest.mark.asyncio
async def test_prune_keeps_all_when_threshold_huge(store):
    await _insert(store, "old", _OLD)
    # max_age_s so large the cutoff predates _OLD → nothing is old enough.
    deleted = await store.delete_older_than(
        now=time.time(), max_age_s=1e15, batch_limit=100
    )
    assert deleted == 0
    assert len(await store.fetch_all()) == 1
