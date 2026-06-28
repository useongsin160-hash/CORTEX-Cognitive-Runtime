"""Phase 6 STEP 2 — DopamineRPE.dry_run() tests."""

from __future__ import annotations

import asyncio

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import DryRunConfig, RPEContext, RPEProposal, RPEReward
from app.rpe.sources import HeuristicOutcomeSource, MockRewardSource


def _ctx(
    trace_id: str,
    category: str | None = "coding",
    response_source: str = "generated",
    **overrides,
) -> RPEContext:
    return RPEContext(
        trace_id=trace_id,
        category=category,
        response_source=response_source,
        **overrides,
    )


def _rpe(*sources, logger=None) -> DopamineRPE:
    if logger is None:
        logger = SpinalLogger()
    return DopamineRPE(sources=list(sources), logger=logger)


class TestDryRunBasic:
    @pytest.mark.asyncio
    async def test_returns_list_of_proposals(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-dr-1")
        proposals = await rpe.dry_run(ctx)
        assert isinstance(proposals, list)
        assert all(isinstance(p, RPEProposal) for p in proposals)

    @pytest.mark.asyncio
    async def test_single_source_one_proposal(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-dr-2")
        proposals = await rpe.dry_run(ctx)
        assert len(proposals) == 1

    @pytest.mark.asyncio
    async def test_two_sources_two_proposals(self) -> None:
        rpe = _rpe(
            MockRewardSource(reward_map={"trace-dr-3": (0.3, 0.9)}),
            HeuristicOutcomeSource(),
        )
        ctx = _ctx("trace-dr-3")
        proposals = await rpe.dry_run(ctx)
        # Both sources should produce a proposal for category=coding.
        assert len(proposals) == 2

    @pytest.mark.asyncio
    async def test_no_aggregation(self) -> None:
        # Each source produces a separate proposal — not merged.
        rpe = _rpe(
            MockRewardSource(reward_map={"trace-dr-agg": (0.3, 0.9)}),
            MockRewardSource(reward_map={"trace-dr-agg": (0.2, 0.6)}),
        )
        ctx = _ctx("trace-dr-agg")
        proposals = await rpe.dry_run(ctx)
        assert len(proposals) == 2
        deltas = [p.proposed_delta for p in proposals]
        # Deltas differ because inputs differ.
        assert deltas[0] != deltas[1]


class TestObservePreserved:
    @pytest.mark.asyncio
    async def test_observe_still_returns_decisions(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-obs-1")
        decisions = await rpe.observe(ctx)
        assert len(decisions) == 1
        assert decisions[0].mode == "observe_only"

    @pytest.mark.asyncio
    async def test_observe_decisions_still_have_no_mutation_fields(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-obs-2")
        decisions = await rpe.observe(ctx)
        for d in decisions:
            assert d.mode == "observe_only"
            assert d.applied is False
            assert d.target is None
            assert d.proposed_delta is None
            assert d.rollback_id is None


class TestCategorySkip:
    @pytest.mark.asyncio
    async def test_no_category_returns_empty(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-skip-1", category=None)
        proposals = await rpe.dry_run(ctx)
        assert proposals == []

    @pytest.mark.asyncio
    async def test_invalid_category_returns_empty(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-skip-2", category="astrophysics")
        proposals = await rpe.dry_run(ctx)
        assert proposals == []


class TestCurrentValues:
    @pytest.mark.asyncio
    async def test_missing_key_gives_none_current_value(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-cv-1")
        proposals = await rpe.dry_run(ctx, current_values={})
        assert len(proposals) == 1
        assert proposals[0].current_value is None
        assert proposals[0].proposed_value is None

    @pytest.mark.asyncio
    async def test_provided_current_value_used(self) -> None:
        rpe = _rpe(MockRewardSource(reward_map={"trace-cv-2": (0.5, 0.8)}))
        ctx = _ctx("trace-cv-2")
        proposals = await rpe.dry_run(ctx, current_values={"category:coding": 0.6})
        assert len(proposals) == 1
        assert proposals[0].current_value == pytest.approx(0.6)
        assert proposals[0].proposed_value is not None

    @pytest.mark.asyncio
    async def test_invalid_current_value_skipped(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-cv-3")
        # current_value=0.05 is below weight_min=0.1 → skip
        proposals = await rpe.dry_run(ctx, current_values={"category:coding": 0.05})
        assert proposals == []


class TestAllProposalsAreUnapplied:
    @pytest.mark.asyncio
    async def test_all_proposals_applied_false(self) -> None:
        rpe = _rpe(
            MockRewardSource(),
            HeuristicOutcomeSource(),
        )
        ctx = _ctx("trace-applied-1")
        proposals = await rpe.dry_run(ctx)
        assert all(p.applied is False for p in proposals)

    @pytest.mark.asyncio
    async def test_all_proposals_target_synapse_weight(self) -> None:
        rpe = _rpe(MockRewardSource())
        ctx = _ctx("trace-target-1")
        proposals = await rpe.dry_run(ctx)
        assert all(p.target == "synapse_weight" for p in proposals)


class TestNoActiveMethods:
    def test_no_active_method(self) -> None:
        # STEP 3.1: apply() exists as a wrapper. active()/.execute() do not.
        rpe = _rpe(MockRewardSource())
        assert not hasattr(rpe, "active")
        assert not hasattr(rpe, "execute_mutation")


class TestDryRunCancellation:
    @pytest.mark.asyncio
    async def test_cancelled_error_reraises(self) -> None:
        class _CancellingSource:
            async def compute_reward(self, context):
                raise asyncio.CancelledError()

        rpe = _rpe(_CancellingSource())
        ctx = _ctx("trace-cancel-dr")
        with pytest.raises(asyncio.CancelledError):
            await rpe.dry_run(ctx)
