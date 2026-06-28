"""Phase 6 STEP 3.1 — active mutation logging tests."""

from __future__ import annotations

import uuid

import pytest

from app.core.logging import SpinalLogger
from app.rpe.models import (
    ActiveMutationConfig,
    RPEContext,
    RPEDecision,
    RPEProposal,
    RPEReward,
)
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.service import RPEMutationService


def _proposal(
    *,
    trace_id: str = "trace-log",
    source: str = "mock",
    confidence: float = 0.6,
    expected_reward: float = 0.3,
    actual_reward: float = 0.9,
    proposed_delta: float = 0.05,
    session_id: str | None = "sess-1",
    category: str | None = "coding",
) -> RPEProposal:
    reward = RPEReward(
        source=source,  # type: ignore[arg-type]
        expected_reward=expected_reward,
        actual_reward=actual_reward,
        confidence=confidence,
    )
    ctx = RPEContext(trace_id=trace_id, session_id=session_id, category=category)
    decision = RPEDecision(reward=reward, context=ctx)
    return RPEProposal(
        decision=decision,
        target="synapse_weight",
        target_key=f"category:{category}" if category else "category:coding",
        current_value=0.5,
        proposed_delta=proposed_delta,
        proposed_value=0.5 + proposed_delta,
        max_delta=0.1,
        rollback_id=str(uuid.uuid4()),
        confidence=confidence,
        applied=False,
    )


def _service(
    *,
    enabled: bool = True,
    initial_weights: dict[tuple[str, str], float] | None = None,
    lock_timeout_ms: float = 1000.0,
) -> tuple[RPEMutationService, SpinalLogger, InMemorySynapseWeightStore]:
    store = InMemorySynapseWeightStore(
        initial_weights if initial_weights is not None else {("sess-1", "coding"): 0.5}
    )
    mutator = SynapseWeightMutator(store=store)
    logger = SpinalLogger()
    # B5: service-unit mutation test — gate is active_enabled.
    config = ActiveMutationConfig(
        active_enabled=enabled,
        min_confidence=0.5,
        min_abs_prediction_error=0.3,
        lock_timeout_ms=lock_timeout_ms,
    )
    svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
    return svc, logger, store


class TestActiveApplied:
    @pytest.mark.asyncio
    async def test_payload_has_required_fields(self) -> None:
        svc, logger, store = _service()
        await svc.apply_proposals([_proposal(trace_id="trace-ap")])
        events = [
            e for e in logger.get_trace("trace-ap")
            if e.event_type == "rpe.active_applied"
        ]
        assert len(events) == 1
        e = events[0]
        assert e.module_name == "rpe_mutation_service"
        for key in (
            "session_id",
            "source",
            "target",
            "target_key",
            "previous_value",
            "proposed_delta",
            "applied_delta",
            "new_value",
            "max_delta",
            "rollback_id",
            "confidence",
            "prediction_error",
            "lock_key",
            "applied_at",
            "current_value_mismatch",
        ):
            assert key in e.payload, key

    @pytest.mark.asyncio
    async def test_lock_key_format(self) -> None:
        svc, logger, store = _service()
        await svc.apply_proposals([_proposal(trace_id="trace-lk")])
        events = [
            e for e in logger.get_trace("trace-lk")
            if e.event_type == "rpe.active_applied"
        ]
        assert events[0].payload["lock_key"] == "synapse_weight:category:coding"


class TestActiveBlocked:
    @pytest.mark.asyncio
    async def test_below_confidence(self) -> None:
        svc, logger, store = _service()
        await svc.apply_proposals(
            [_proposal(trace_id="trace-bc", confidence=0.3)]
        )
        events = [
            e for e in logger.get_trace("trace-bc")
            if e.event_type == "rpe.active_blocked"
        ]
        assert events[0].payload["reason"] == "below_confidence"

    @pytest.mark.asyncio
    async def test_below_prediction_error(self) -> None:
        svc, logger, store = _service()
        await svc.apply_proposals(
            [_proposal(
                trace_id="trace-bpe",
                expected_reward=0.5,
                actual_reward=0.6,
            )]
        )
        events = [
            e for e in logger.get_trace("trace-bpe")
            if e.event_type == "rpe.active_blocked"
        ]
        assert events[0].payload["reason"] == "below_prediction_error"

    @pytest.mark.asyncio
    async def test_duplicate_target(self) -> None:
        svc, logger, store = _service()
        p1 = _proposal(trace_id="trace-dup", source="mock", confidence=0.7)
        p2 = _proposal(trace_id="trace-dup", source="heuristic", confidence=0.6)
        await svc.apply_proposals([p1, p2])
        events = [
            e for e in logger.get_trace("trace-dup")
            if e.event_type == "rpe.active_blocked"
        ]
        dup_events = [e for e in events if e.payload["reason"] == "duplicate_target"]
        assert len(dup_events) == 1
        assert "competing_rollback_id" in dup_events[0].payload

    @pytest.mark.asyncio
    async def test_lock_timeout(self) -> None:
        svc, logger, store = _service(lock_timeout_ms=50.0)
        lock = svc._get_or_create_key_lock("synapse_weight:category:coding")  # type: ignore[attr-defined]
        await lock.acquire()
        try:
            await svc.apply_proposals([_proposal(trace_id="trace-lt")])
            events = [
                e for e in logger.get_trace("trace-lt")
                if e.event_type == "rpe.active_blocked"
            ]
            assert any(e.payload["reason"] == "lock_timeout" for e in events)
        finally:
            lock.release()


class TestActiveSkipped:
    @pytest.mark.asyncio
    async def test_disabled_reason(self) -> None:
        svc, logger, store = _service(enabled=False)
        await svc.apply_proposals([_proposal(trace_id="trace-sk")])
        events = [
            e for e in logger.get_trace("trace-sk")
            if e.event_type == "rpe.active_skipped"
        ]
        assert events[0].payload["reason"] == "disabled"


class TestActiveRollback:
    @pytest.mark.asyncio
    async def test_payload(self) -> None:
        svc, logger, store = _service()
        records = await svc.apply_proposals([_proposal(trace_id="trace-rb")])
        rb_id = records[0].rollback_id
        await svc.rollback(rb_id)
        events = [
            e for e in logger.get_trace("trace-rb")
            if e.event_type == "rpe.active_rollback"
        ]
        assert len(events) == 1
        for key in (
            "rollback_id",
            "target_key",
            "previous_value",
            "current_value_before_rollback",
            "restored_value",
            "rolled_back_at",
        ):
            assert key in events[0].payload, key


class TestActiveError:
    @pytest.mark.asyncio
    async def test_missing_weight_logs_error(self) -> None:
        svc, logger, store = _service(initial_weights={})
        await svc.apply_proposals([_proposal(trace_id="trace-err")])
        events = [
            e for e in logger.get_trace("trace-err")
            if e.event_type == "rpe.active_error"
        ]
        assert len(events) == 1
        assert "phase" in events[0].payload
        assert events[0].payload["phase"] == "read"


class TestTraceIdPropagation:
    @pytest.mark.asyncio
    async def test_all_events_carry_trace_id(self) -> None:
        svc, logger, store = _service()
        records = await svc.apply_proposals([_proposal(trace_id="trace-tid")])
        await svc.rollback(records[0].rollback_id)
        events = logger.get_trace("trace-tid")
        assert all(e.trace_id == "trace-tid" for e in events)
