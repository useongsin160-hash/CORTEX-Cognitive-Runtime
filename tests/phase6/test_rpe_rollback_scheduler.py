"""B4 — AsyncIOScheduler timeout rollback tests.

Covers: schedule registers a pending job, confirm cancels it (no rollback),
the fire path calls the service rollback (previous_value restored), one real
short-timeout integration (auto-rollback actually fires), manual rollback stays
intact, the frozen no-schedule path, and the decay-independent previous_value
semantics.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import ActiveMutationConfig, DryRunConfig, RPEContext
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.rollback_scheduler import RollbackScheduler
from app.rpe.service import RPEMutationService
from app.rpe.sources import MockRewardSource


def _service(scheduler, *, active=True):
    store = InMemorySynapseWeightStore({("s1", "coding"): 0.5})
    return RPEMutationService(
        mutator=SynapseWeightMutator(store=store),
        logger=SpinalLogger(),
        config=ActiveMutationConfig(active_enabled=active),
        rollback_scheduler=scheduler,
    ), store


async def _apply(service, *, trace="t1"):
    rpe = DopamineRPE(
        sources=[MockRewardSource(reward_map={trace: (0.3, 0.9)})],
        logger=SpinalLogger(),
        dry_run_config=DryRunConfig(),
    )
    ctx = RPEContext(trace_id=trace, session_id="s1", category="coding", difficulty=2)
    return await rpe.apply(ctx, {"category:coding": 0.5}, mutation_service=service)


# ── schedule / confirm registry ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_apply_schedules_pending_rollback():
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=300.0)
    scheduler.start()
    try:
        service, _ = _service(scheduler)
        recs = await _apply(service)
        assert len(recs) == 1
        assert recs[0].rollback_id in scheduler.pending
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_confirm_cancels_pending_rollback():
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=300.0)
    scheduler.start()
    try:
        service, store = _service(scheduler)
        recs = await _apply(service)
        rid = recs[0].rollback_id
        assert service.confirm_mutation(rid) is True
        assert rid not in scheduler.pending
        # confirmed → value NOT rolled back.
        assert await store.read_weight("s1", "coding") == pytest.approx(0.56)
        # second confirm is a no-op.
        assert service.confirm_mutation(rid) is False
    finally:
        scheduler.shutdown()


# ── fire path restores previous_value (decay-independent) ───────────────────
@pytest.mark.asyncio
async def test_fire_rolls_back_to_previous_value():
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=300.0)
    scheduler.start()
    try:
        service, store = _service(scheduler)
        recs = await _apply(service)
        rid = recs[0].rollback_id
        assert await store.read_weight("s1", "coding") == pytest.approx(0.56)
        # drive the job body directly (deterministic — no wall-clock wait).
        await scheduler._fire(rid, "t1", service.rollback)
        # previous_value (0.5) restored — "undo this mutation", not a time-rewind.
        assert await store.read_weight("s1", "coding") == pytest.approx(0.5)
        assert rid not in scheduler.pending
        assert service.get_record(rid).rollback_status == "rolled_back"
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_short_timeout_auto_rollback_fires():
    """One real-timing integration: a tiny timeout actually triggers the job."""
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=0.1)
    scheduler.start()
    try:
        service, store = _service(scheduler)
        recs = await _apply(service)
        rid = recs[0].rollback_id
        # wait past the timeout for the scheduler to run the job.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if rid not in scheduler.pending:
                break
        assert rid not in scheduler.pending
        assert await store.read_weight("s1", "coding") == pytest.approx(0.5)
    finally:
        scheduler.shutdown()


# ── manual rollback preserved / no-scheduler / frozen ───────────────────────
@pytest.mark.asyncio
async def test_manual_rollback_still_works_with_scheduler():
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=300.0)
    scheduler.start()
    try:
        service, store = _service(scheduler)
        recs = await _apply(service)
        out = await service.rollback(recs[0].rollback_id)  # manual
        assert out.rollback_status == "rolled_back"
        assert await store.read_weight("s1", "coding") == pytest.approx(0.5)
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_no_scheduler_confirm_is_noop():
    service, _ = _service(None)
    recs = await _apply(service)
    assert len(recs) == 1
    assert service.confirm_mutation(recs[0].rollback_id) is False


@pytest.mark.asyncio
async def test_frozen_inactive_schedules_nothing():
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=300.0)
    scheduler.start()
    try:
        service, _ = _service(scheduler, active=False)  # B13-style freeze
        recs = await _apply(service)
        assert recs == []
        assert scheduler.pending == set()
    finally:
        scheduler.shutdown()


# ── construction guard / lifecycle ──────────────────────────────────────────
def test_invalid_timeout_rejected():
    with pytest.raises(ValueError, match="timeout_s"):
        RollbackScheduler(SpinalLogger(), timeout_s=0.0)


@pytest.mark.asyncio
async def test_start_shutdown_idempotent():
    # start() attaches to the running loop (as it does in lifespan).
    scheduler = RollbackScheduler(SpinalLogger(), timeout_s=300.0)
    scheduler.start()
    scheduler.start()  # idempotent
    scheduler.shutdown()
    scheduler.shutdown()  # idempotent
