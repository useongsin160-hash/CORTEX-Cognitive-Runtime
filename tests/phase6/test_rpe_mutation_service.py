"""Phase 6 STEP 3.1 — RPEMutationService tests."""

from __future__ import annotations

import asyncio
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
    trace_id: str = "trace-svc",
    session_id: str = "sess-1",
    category: str = "coding",
    source: str = "mock",
    confidence: float = 0.5,
    expected_reward: float = 0.3,
    actual_reward: float = 0.9,
    proposed_delta: float = 0.05,
    max_delta: float = 0.1,
) -> RPEProposal:
    # prediction_error = actual - expected
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
        target_key=f"category:{category}",
        current_value=0.5,
        proposed_delta=proposed_delta,
        proposed_value=min(max(0.5 + proposed_delta, 0.1), 1.0),
        max_delta=max_delta,
        rollback_id=str(uuid.uuid4()),
        confidence=confidence,
        applied=False,
    )


def _service(
    *,
    enabled: bool = True,
    initial_weights: dict[tuple[str, str], float] | None = None,
    min_confidence: float = 0.5,
    min_abs_pe: float = 0.3,
    lock_timeout_ms: float = 1000.0,
) -> tuple[RPEMutationService, InMemorySynapseWeightStore, SpinalLogger]:
    store = InMemorySynapseWeightStore(
        initial=initial_weights if initial_weights is not None else {("sess-1", "coding"): 0.5}
    )
    mutator = SynapseWeightMutator(store=store)
    logger = SpinalLogger()
    # B5: this is a service-unit test (apply_proposals directly) — the mutation
    # gate is active_enabled. The helper's `enabled` param means "active on".
    config = ActiveMutationConfig(
        active_enabled=enabled,
        min_confidence=min_confidence,
        min_abs_prediction_error=min_abs_pe,
        lock_timeout_ms=lock_timeout_ms,
    )
    svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
    return svc, store, logger


class TestDisabledByDefault:
    @pytest.mark.asyncio
    async def test_default_config_disabled(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
        mutator = SynapseWeightMutator(store=store)
        svc = RPEMutationService(mutator=mutator, logger=SpinalLogger())
        assert svc.config.active_enabled is False

    @pytest.mark.asyncio
    async def test_disabled_returns_empty_and_logs_skipped(self) -> None:
        svc, store, logger = _service(enabled=False)
        proposal = _proposal(trace_id="trace-disabled")
        records = await svc.apply_proposals([proposal])
        assert records == []
        # Store unchanged.
        assert await store.read_weight("sess-1", "coding") == 0.5
        events = [
            e
            for e in logger.get_trace("trace-disabled")
            if e.event_type == "rpe.active_skipped"
        ]
        assert len(events) == 1
        assert events[0].payload["reason"] == "disabled"


class TestThresholds:
    @pytest.mark.asyncio
    async def test_below_confidence_blocked(self) -> None:
        svc, store, logger = _service()
        # confidence below min_confidence=0.5
        p = _proposal(trace_id="svc-trace-below-conf", confidence=0.3)
        records = await svc.apply_proposals([p])
        assert records == []
        assert await store.read_weight("sess-1", "coding") == 0.5
        events = [
            e for e in logger.get_trace("svc-trace-below-conf")
            if e.event_type == "rpe.active_blocked"
        ]
        assert len(events) == 1
        assert events[0].payload["reason"] == "below_confidence"

    @pytest.mark.asyncio
    async def test_below_prediction_error_blocked(self) -> None:
        svc, store, logger = _service()
        # pe = 0.6 - 0.5 = 0.1 < 0.3
        p = _proposal(
            trace_id="svc-trace-below-pe",
            confidence=0.6,
            expected_reward=0.5,
            actual_reward=0.6,
        )
        records = await svc.apply_proposals([p])
        assert records == []
        events = [
            e for e in logger.get_trace("svc-trace-below-pe")
            if e.event_type == "rpe.active_blocked"
        ]
        assert events[0].payload["reason"] == "below_prediction_error"

    @pytest.mark.asyncio
    async def test_zero_delta_blocked(self) -> None:
        svc, store, logger = _service()
        # Manually crafted: proposed_delta=0 (corner case)
        p = _proposal(
            trace_id="trace-zd",
            confidence=0.6,
            expected_reward=0.4,
            actual_reward=0.8,
            proposed_delta=0.0,
        )
        # Need to bypass RPEProposal validation? proposed_delta=0 is allowed.
        records = await svc.apply_proposals([p])
        assert records == []


class TestSuccessfulMutation:
    @pytest.mark.asyncio
    async def test_qualifying_proposal_applies(self) -> None:
        svc, store, logger = _service()
        p = _proposal(
            trace_id="trace-ok",
            confidence=0.6,
            expected_reward=0.3,
            actual_reward=0.9,
            proposed_delta=0.05,
        )
        records = await svc.apply_proposals([p])
        assert len(records) == 1
        record = records[0]
        assert record.previous_value == 0.5
        assert record.new_value == pytest.approx(0.55)
        # Store updated.
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_record_stored_in_service(self) -> None:
        svc, store, logger = _service()
        p = _proposal(
            trace_id="trace-store",
            confidence=0.6,
            expected_reward=0.3,
            actual_reward=0.9,
        )
        records = await svc.apply_proposals([p])
        record = records[0]
        assert svc.get_record(record.rollback_id) is record


class TestDuplicateTarget:
    @pytest.mark.asyncio
    async def test_duplicate_in_same_call_one_applied(self) -> None:
        svc, store, logger = _service()
        # Two proposals same trace + same target.
        # Both qualify, but only one wins.
        p1 = _proposal(trace_id="trace-dup", source="mock", confidence=0.6, proposed_delta=0.04)
        p2 = _proposal(trace_id="trace-dup", source="heuristic", confidence=0.5, proposed_delta=0.06)
        records = await svc.apply_proposals([p1, p2])
        assert len(records) == 1
        # confidence 0.6 wins
        assert records[0].proposal is p1
        # Blocked event for p2.
        events = [
            e for e in logger.get_trace("trace-dup")
            if e.event_type == "rpe.active_blocked"
        ]
        assert any(e.payload["reason"] == "duplicate_target" for e in events)

    @pytest.mark.asyncio
    async def test_second_call_blocked_for_same_trace_target(self) -> None:
        svc, store, logger = _service()
        p1 = _proposal(trace_id="trace-dup2", confidence=0.6, proposed_delta=0.04)
        records1 = await svc.apply_proposals([p1])
        assert len(records1) == 1

        p2 = _proposal(trace_id="trace-dup2", confidence=0.7, proposed_delta=0.05)
        records2 = await svc.apply_proposals([p2])
        assert records2 == []
        events = [
            e for e in logger.get_trace("trace-dup2")
            if e.event_type == "rpe.active_blocked"
        ]
        # 1 block: second call's p2.
        assert sum(1 for e in events if e.payload["reason"] == "duplicate_target") == 1


class TestWinnerSelection:
    @pytest.mark.asyncio
    async def test_higher_confidence_wins(self) -> None:
        svc, store, logger = _service()
        p_low = _proposal(trace_id="trace-w1", source="mock", confidence=0.5, proposed_delta=0.05)
        p_high = _proposal(trace_id="trace-w1", source="heuristic", confidence=0.8, proposed_delta=0.04)
        records = await svc.apply_proposals([p_low, p_high])
        assert records[0].proposal is p_high

    @pytest.mark.asyncio
    async def test_tie_confidence_mock_wins(self) -> None:
        svc, store, logger = _service()
        p_mock = _proposal(trace_id="trace-w2", source="mock", confidence=0.6, proposed_delta=0.04)
        p_heur = _proposal(trace_id="trace-w2", source="heuristic", confidence=0.6, proposed_delta=0.05)
        records = await svc.apply_proposals([p_mock, p_heur])
        assert records[0].proposal is p_mock

    @pytest.mark.asyncio
    async def test_tie_confidence_and_source_larger_delta_wins(self) -> None:
        svc, store, logger = _service()
        p_small = _proposal(trace_id="trace-w3", source="mock", confidence=0.6, proposed_delta=0.04)
        p_large = _proposal(trace_id="trace-w3", source="mock", confidence=0.6, proposed_delta=0.06)
        records = await svc.apply_proposals([p_small, p_large])
        assert records[0].proposal is p_large


class TestRollback:
    @pytest.mark.asyncio
    async def test_manual_rollback_restores_value(self) -> None:
        svc, store, logger = _service()
        p = _proposal(trace_id="trace-rb", confidence=0.6, proposed_delta=0.05)
        records = await svc.apply_proposals([p])
        rb_id = records[0].rollback_id
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.55)

        rolled = await svc.rollback(rb_id)
        assert rolled is not None
        assert rolled.rollback_status == "rolled_back"
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_rollback_unknown_id_returns_none(self) -> None:
        svc, store, logger = _service()
        result = await svc.rollback("not-a-known-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_rollback_already_rolled_back_idempotent(self) -> None:
        svc, store, logger = _service()
        p = _proposal(trace_id="trace-rb2", confidence=0.6, proposed_delta=0.05)
        records = await svc.apply_proposals([p])
        rb_id = records[0].rollback_id
        await svc.rollback(rb_id)
        second = await svc.rollback(rb_id)
        # Returns the rolled_back record unchanged.
        assert second is not None
        assert second.rollback_status == "rolled_back"
        # Value still at previous_value.
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.5)


class TestLocking:
    @pytest.mark.asyncio
    async def test_same_category_serialized(self) -> None:
        # Two different traces, same category — must serialize.
        svc, store, logger = _service(
            initial_weights={
                ("sess-1", "coding"): 0.5,
                ("sess-2", "coding"): 0.5,
            },
        )
        p1 = _proposal(
            trace_id="t1", session_id="sess-1", confidence=0.6, proposed_delta=0.05
        )
        p2 = _proposal(
            trace_id="t2", session_id="sess-2", confidence=0.6, proposed_delta=0.05
        )
        results = await asyncio.gather(
            svc.apply_proposals([p1]),
            svc.apply_proposals([p2]),
        )
        # Both should apply but serialized.
        assert len(results[0]) == 1
        assert len(results[1]) == 1
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.55)
        assert await store.read_weight("sess-2", "coding") == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_lock_timeout_blocks(self) -> None:
        # Pre-acquire the same lock to force timeout.
        svc, store, logger = _service(lock_timeout_ms=50.0)
        lock = svc._get_or_create_key_lock("synapse_weight:category:coding")  # type: ignore[attr-defined]
        await lock.acquire()
        try:
            p = _proposal(trace_id="trace-lt", confidence=0.6, proposed_delta=0.05)
            records = await svc.apply_proposals([p])
            assert records == []
            events = [
                e for e in logger.get_trace("trace-lt")
                if e.event_type == "rpe.active_blocked"
            ]
            assert any(e.payload["reason"] == "lock_timeout" for e in events)
        finally:
            lock.release()


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_missing_weight_logs_active_error(self) -> None:
        svc, store, logger = _service(initial_weights={})
        p = _proposal(trace_id="trace-mw", confidence=0.6, proposed_delta=0.05)
        records = await svc.apply_proposals([p])
        assert records == []
        events = [
            e for e in logger.get_trace("trace-mw")
            if e.event_type == "rpe.active_error"
        ]
        assert len(events) == 1
        assert "Missing" in events[0].payload["error_type"] or events[0].payload["error_type"] == "MissingWeight"

    @pytest.mark.asyncio
    async def test_session_id_none_logs_active_error(self) -> None:
        svc, store, logger = _service()
        reward = RPEReward(
            source="mock", expected_reward=0.3, actual_reward=0.9, confidence=0.6
        )
        ctx = RPEContext(trace_id="trace-ns", session_id=None, category="coding")
        d = RPEDecision(reward=reward, context=ctx)
        p = RPEProposal(
            decision=d,
            target="synapse_weight",
            target_key="category:coding",
            current_value=None,
            proposed_delta=0.05,
            proposed_value=None,
            max_delta=0.1,
            rollback_id=str(uuid.uuid4()),
            confidence=0.6,
            applied=False,
        )
        records = await svc.apply_proposals([p])
        assert records == []
        events = [
            e for e in logger.get_trace("trace-ns")
            if e.event_type == "rpe.active_error"
        ]
        assert any("session_id" in e.payload["error"] for e in events)


class TestCurrentValueMismatch:
    @pytest.mark.asyncio
    async def test_stale_current_values_flagged(self) -> None:
        # Store has 0.5, but caller's hint says 0.7.
        svc, store, logger = _service(initial_weights={("sess-1", "coding"): 0.5})
        p = _proposal(trace_id="trace-stale", confidence=0.6, proposed_delta=0.05)
        records = await svc.apply_proposals(
            [p], current_values={"category:coding": 0.7}
        )
        assert len(records) == 1
        assert records[0].current_value_mismatch is True
        # Mutation still uses store value 0.5, not the stale hint 0.7.
        assert records[0].previous_value == 0.5
        assert records[0].new_value == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_matching_current_values_no_flag(self) -> None:
        svc, store, logger = _service(initial_weights={("sess-1", "coding"): 0.5})
        p = _proposal(trace_id="trace-match", confidence=0.6, proposed_delta=0.05)
        records = await svc.apply_proposals(
            [p], current_values={"category:coding": 0.5}
        )
        assert records[0].current_value_mismatch is False
