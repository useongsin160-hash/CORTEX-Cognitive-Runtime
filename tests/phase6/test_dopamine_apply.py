"""Phase 6 STEP 3.1 — DopamineRPE.apply() tests."""

from __future__ import annotations

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import ActiveMutationConfig, RPEContext
from app.rpe.mutators import InMemorySynapseWeightStore, SynapseWeightMutator
from app.rpe.service import RPEMutationService
from app.rpe.sources import MockRewardSource


def _rpe_and_service(*, enabled: bool = True) -> tuple[DopamineRPE, RPEMutationService, InMemorySynapseWeightStore, SpinalLogger]:
    logger = SpinalLogger()
    rpe = DopamineRPE(
        sources=[MockRewardSource(reward_map={"trace-apply": (0.3, 0.9)})],
        logger=logger,
    )
    store = InMemorySynapseWeightStore({("sess-1", "coding"): 0.5})
    mutator = SynapseWeightMutator(store=store)
    # B5: dopamine.apply() delegates to service.apply_proposals — mutation gate
    # is active_enabled (dopamine.apply does not consult the pipeline observe gate).
    config = ActiveMutationConfig(
        active_enabled=enabled,
        min_confidence=0.5,
        min_abs_prediction_error=0.3,
    )
    svc = RPEMutationService(mutator=mutator, logger=logger, config=config)
    return rpe, svc, store, logger


def _ctx(
    trace_id: str = "trace-apply",
    session_id: str = "sess-1",
    category: str = "coding",
) -> RPEContext:
    return RPEContext(
        trace_id=trace_id, session_id=session_id, category=category,
    )


class TestApplyDelegation:
    @pytest.mark.asyncio
    async def test_no_service_returns_empty_and_logs(self) -> None:
        logger = SpinalLogger()
        rpe = DopamineRPE(sources=[MockRewardSource()], logger=logger)
        records = await rpe.apply(_ctx("trace-no-svc"), mutation_service=None)
        assert records == []
        events = [
            e for e in logger.get_trace("trace-no-svc")
            if e.event_type == "rpe.active_skipped"
        ]
        assert any(e.payload["reason"] == "no_service" for e in events)

    @pytest.mark.asyncio
    async def test_disabled_service_returns_empty(self) -> None:
        rpe, svc, store, logger = _rpe_and_service(enabled=False)
        records = await rpe.apply(_ctx(), mutation_service=svc)
        assert records == []
        assert await store.read_weight("sess-1", "coding") == 0.5

    @pytest.mark.asyncio
    async def test_enabled_service_applies(self) -> None:
        rpe, svc, store, logger = _rpe_and_service(enabled=True)
        records = await rpe.apply(
            _ctx(), current_values={"category:coding": 0.5}, mutation_service=svc,
        )
        assert len(records) == 1
        # MockRewardSource returns (0.3, 0.9) → pe=0.6, conf=1.0
        # delta = 0.6 * 1.0 * 0.1 = 0.06
        # 0.5 + 0.06 = 0.56
        assert records[0].new_value == pytest.approx(0.56)
        assert await store.read_weight("sess-1", "coding") == pytest.approx(0.56)

    @pytest.mark.asyncio
    async def test_no_proposals_returns_empty(self) -> None:
        # Use a context with no category → dry_run produces no proposals.
        rpe, svc, store, logger = _rpe_and_service(enabled=True)
        records = await rpe.apply(
            _ctx(trace_id="trace-no-prop", category="general")
            if False  # placeholder
            else RPEContext(trace_id="trace-no-prop", session_id="sess-1", category=None),
            mutation_service=svc,
        )
        assert records == []


class TestObserveAndDryRunStillWork:
    @pytest.mark.asyncio
    async def test_observe_unchanged(self) -> None:
        rpe, svc, store, logger = _rpe_and_service()
        decisions = await rpe.observe(_ctx(trace_id="trace-obs-after-apply"))
        assert len(decisions) == 1
        assert decisions[0].mode == "observe_only"
        assert decisions[0].applied is False

    @pytest.mark.asyncio
    async def test_dry_run_unchanged(self) -> None:
        rpe, svc, store, logger = _rpe_and_service()
        proposals = await rpe.dry_run(
            _ctx(trace_id="trace-dr-after-apply"),
            current_values={"category:coding": 0.5},
        )
        assert all(p.applied is False for p in proposals)
        assert all(p.target == "synapse_weight" for p in proposals)
