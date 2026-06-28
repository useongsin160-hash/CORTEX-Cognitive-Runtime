"""Phase 6 STEP 3.1 — active mutation safety tests."""

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
    trace_id: str = "trace-safe",
    session_id: str = "sess-1",
    category: str = "coding",
    source: str = "mock",
    confidence: float = 0.6,
    expected_reward: float = 0.3,
    actual_reward: float = 0.9,
    proposed_delta: float = 0.05,
    max_delta: float = 0.1,
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
        target_key=f"category:{category}",
        current_value=0.5,
        proposed_delta=proposed_delta,
        proposed_value=min(max(0.5 + proposed_delta, 0.1), 1.0),
        max_delta=max_delta,
        rollback_id=str(uuid.uuid4()),
        confidence=confidence,
        applied=False,
    )


def _enabled_service(
    initial: dict[tuple[str, str], float] | None = None,
) -> tuple[RPEMutationService, InMemorySynapseWeightStore, SpinalLogger]:
    store = InMemorySynapseWeightStore(initial if initial is not None else {("sess-1", "coding"): 0.5})
    mutator = SynapseWeightMutator(store=store)
    logger = SpinalLogger()
    # B5: service-unit mutation test — gate is active_enabled.
    config = ActiveMutationConfig(
        active_enabled=True,
        min_confidence=0.5,
        min_abs_prediction_error=0.3,
        lock_timeout_ms=1000.0,
    )
    return RPEMutationService(mutator, logger, config), store, logger


class TestMaxDeltaSafety:
    @pytest.mark.asyncio
    async def test_applied_delta_within_max_delta(self) -> None:
        svc, store, logger = _enabled_service()
        records = await svc.apply_proposals([_proposal()])
        for r in records:
            assert abs(r.applied_delta) <= r.proposal.max_delta + 1e-9


class TestPerTraceTargetSingleApply:
    @pytest.mark.asyncio
    async def test_same_trace_same_target_only_one_applied(self) -> None:
        svc, store, logger = _enabled_service()
        proposals = [
            _proposal(trace_id="trace-st", source="mock", confidence=0.7),
            _proposal(trace_id="trace-st", source="heuristic", confidence=0.55),
        ]
        records = await svc.apply_proposals(proposals)
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_different_traces_independent(self) -> None:
        svc, store, logger = _enabled_service()
        await svc.apply_proposals([_proposal(trace_id="trace-A")])
        # Different trace, same target — should apply.
        records = await svc.apply_proposals([_proposal(trace_id="trace-B")])
        assert len(records) == 1


class TestRollbackSafety:
    @pytest.mark.asyncio
    async def test_rollback_exactly_restores(self) -> None:
        svc, store, logger = _enabled_service(
            initial={("sess-1", "coding"): 0.42},
        )
        records = await svc.apply_proposals([_proposal(trace_id="trace-rb")])
        rb_id = records[0].rollback_id
        await svc.rollback(rb_id)
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.42)


class TestConcurrentLockSafety:
    @pytest.mark.asyncio
    async def test_concurrent_same_category_no_corruption(self) -> None:
        # 5 concurrent applies on same category but different traces.
        svc, store, logger = _enabled_service(
            initial={(f"sess-{i}", "coding"): 0.5 for i in range(5)},
        )
        proposals = [
            _proposal(
                trace_id=f"t{i}",
                session_id=f"sess-{i}",
                source="mock",
                confidence=0.7,
                proposed_delta=0.05,
            )
            for i in range(5)
        ]
        results = await asyncio.gather(
            *[svc.apply_proposals([p]) for p in proposals]
        )
        # All should apply.
        applied = [r for batch in results for r in batch]
        assert len(applied) == 5
        # Each session's weight should be exactly 0.55.
        for i in range(5):
            v = await store.read_weight(f"sess-{i}", "coding")
            assert v == pytest.approx(0.55), f"sess-{i}"


class TestDisabledNoMutation:
    @pytest.mark.asyncio
    async def test_disabled_does_not_touch_store(self) -> None:
        store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
        mutator = SynapseWeightMutator(store=store)
        logger = SpinalLogger()
        svc = RPEMutationService(
            mutator,
            logger,
            ActiveMutationConfig(active_enabled=False),
        )
        await svc.apply_proposals([_proposal()])
        assert await store.read_weight("sess-1", "coding") == 0.5


class TestRPEDecisionInvariantPreserved:
    @pytest.mark.asyncio
    async def test_records_carry_observe_only_decisions(self) -> None:
        svc, store, logger = _enabled_service()
        records = await svc.apply_proposals([_proposal(trace_id="trace-inv")])
        for r in records:
            d = r.proposal.decision
            assert d.mode == "observe_only"
            assert d.applied is False
            assert d.target is None
            assert d.proposed_delta is None
            assert d.rollback_id is None


class TestNoAutoRollbackScheduler:
    def test_service_has_no_scheduler_attributes(self) -> None:
        store = InMemorySynapseWeightStore()
        mutator = SynapseWeightMutator(store=store)
        svc = RPEMutationService(mutator, SpinalLogger())
        # Hard rule: no auto rollback scheduler in STEP 3.1.
        assert not hasattr(svc, "_scheduler")
        assert not hasattr(svc, "schedule_rollback")
        assert not hasattr(svc, "start_scheduler")
