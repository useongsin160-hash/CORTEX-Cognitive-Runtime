"""B3a — RPE mutation record persistence tests.

Covers: persist/fetch roundtrip across a fresh store instance (restart sim),
rollback status persistence, single-apply preserved (one row per winner),
fail-open on DB error, and the frozen / no-store no-op paths.
"""
from __future__ import annotations

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import ActiveMutationConfig, DryRunConfig, RPEContext
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.record_store import RPERecordStore
from app.rpe.service import RPEMutationService
from app.rpe.sources import MockRewardSource


def _db_url(tmp_path, name: str = "rpe.db") -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def _service(record_store, *, active=True):
    store = InMemorySynapseWeightStore({("s1", "coding"): 0.5})
    return RPEMutationService(
        mutator=SynapseWeightMutator(store=store),
        logger=SpinalLogger(),
        config=ActiveMutationConfig(active_enabled=active),
        record_store=record_store,
    )


async def _apply(service, *, sources, trace="t1"):
    rpe = DopamineRPE(sources=sources, logger=SpinalLogger(), dry_run_config=DryRunConfig())
    ctx = RPEContext(trace_id=trace, session_id="s1", category="coding", difficulty=2)
    return await rpe.apply(ctx, {"category:coding": 0.5}, mutation_service=service)


# ── store-level roundtrip (restart sim) ─────────────────────────────────────
@pytest.mark.asyncio
async def test_persist_roundtrip_across_instances(tmp_path):
    url = _db_url(tmp_path)
    service = _service(RPERecordStore(url))
    records = await _apply(service, sources=[MockRewardSource(reward_map={"t1": (0.3, 0.9)})])
    assert len(records) == 1
    rec = records[0]

    # fresh instance over the SAME db file = restart.
    reopened = RPERecordStore(url)
    row = await reopened.fetch(rec.rollback_id)
    assert row is not None
    assert row["rollback_id"] == rec.rollback_id
    assert row["trace_id"] == "t1"
    assert row["session_id"] == "s1"
    assert row["category"] == "coding"
    assert row["difficulty"] == 2
    assert row["target"] == "synapse_weight"
    assert row["target_key"] == "category:coding"
    assert row["previous_value"] == pytest.approx(0.5)
    assert row["new_value"] == pytest.approx(0.56)
    assert row["rollback_status"] == "available"
    assert row["persisted_at"]  # wall-clock ISO present
    assert row["expires_at"] is None  # B4 populates this later


@pytest.mark.asyncio
async def test_rollback_status_persisted(tmp_path):
    url = _db_url(tmp_path)
    store = RPERecordStore(url)
    service = _service(store)
    records = await _apply(service, sources=[MockRewardSource(reward_map={"t1": (0.3, 0.9)})])
    rid = records[0].rollback_id

    await service.rollback(rid)

    row = await RPERecordStore(url).fetch(rid)
    assert row["rollback_status"] == "rolled_back"


# ── single-apply preserved ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_single_apply_persists_one_row(tmp_path):
    """Two qualifying sources for the same (trace, target) → service selects ONE
    winner; persistence writes exactly one row (raw, not aggregated)."""
    url = _db_url(tmp_path)
    store = RPERecordStore(url)
    service = _service(store)
    # two mock sources, both keyed to the trace → two proposals, same target_key.
    records = await _apply(
        service,
        sources=[
            MockRewardSource(reward_map={"t1": (0.3, 0.9)}),
            MockRewardSource(reward_map={"t1": (0.2, 0.95)}),
        ],
    )
    assert len(records) == 1  # one winner applied
    rows = await store.fetch_all()
    assert len(rows) == 1  # one row persisted


# ── fail-open ────────────────────────────────────────────────────────────────
class _RaisingStore:
    async def persist(self, record):
        raise RuntimeError("db down")

    async def update_status(self, rollback_id, status):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_persist_failure_is_fail_open(tmp_path):
    """A persistence error must NOT break the (in-memory) mutation apply."""
    service = _service(_RaisingStore())
    records = await _apply(service, sources=[MockRewardSource(reward_map={"t1": (0.3, 0.9)})])
    assert len(records) == 1  # apply succeeded despite persist raising
    # in-memory record still queryable + manual rollback still works.
    out = await service.rollback(records[0].rollback_id)
    assert out is not None and out.rollback_status == "rolled_back"


# ── no-store / frozen no-op ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_none_store_no_persist(tmp_path):
    service = _service(None)  # record_store=None → in-memory only
    records = await _apply(service, sources=[MockRewardSource(reward_map={"t1": (0.3, 0.9)})])
    assert len(records) == 1  # works, nothing persisted (no store)


@pytest.mark.asyncio
async def test_frozen_inactive_persists_nothing(tmp_path):
    """active_enabled=False (freeze) → no mutation applies → no rows."""
    url = _db_url(tmp_path)
    store = RPERecordStore(url)
    service = _service(store, active=False)
    records = await _apply(service, sources=[MockRewardSource(reward_map={"t1": (0.3, 0.9)})])
    assert records == []
    assert await store.fetch_all() == []


# ── store unit edges ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_update_status_missing_rollback_id_noop(tmp_path):
    store = RPERecordStore(_db_url(tmp_path))
    await store.update_status("does-not-exist", "rolled_back")  # no error
    assert await store.fetch("does-not-exist") is None


@pytest.mark.asyncio
async def test_fetch_all_empty_on_fresh_db(tmp_path):
    store = RPERecordStore(_db_url(tmp_path))
    assert await store.fetch_all() == []
