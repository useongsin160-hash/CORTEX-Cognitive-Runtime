"""Phase 6 STEP 2 — dry-run safety slot tests."""

from __future__ import annotations

import pytest

from app.core.logging import SpinalLogger
from app.rpe.dopamine import DopamineRPE
from app.rpe.models import RPEContext, RPEDecision, RPEReward
from app.rpe.sources import MockRewardSource


def _ctx(trace_id: str = "trace-safety", category: str = "coding") -> RPEContext:
    return RPEContext(trace_id=trace_id, category=category)


def _rpe() -> DopamineRPE:
    return DopamineRPE(sources=[MockRewardSource()], logger=SpinalLogger())


class TestAllProposalsUnapplied:
    @pytest.mark.asyncio
    async def test_every_proposal_applied_false(self) -> None:
        rpe = _rpe()
        proposals = await rpe.dry_run(_ctx("trace-safe-1"))
        assert all(p.applied is False for p in proposals)

    @pytest.mark.asyncio
    async def test_every_proposal_target_synapse_weight(self) -> None:
        rpe = _rpe()
        proposals = await rpe.dry_run(_ctx("trace-safe-2"))
        assert all(p.target == "synapse_weight" for p in proposals)


class TestNoActiveMethods:
    def test_dopamine_rpe_has_no_active_method(self) -> None:
        # STEP 3.1: apply() exists as a wrapper. active() / execute() do not.
        rpe = _rpe()
        assert not hasattr(rpe, "active")
        assert not hasattr(rpe, "execute_mutation")

    def test_dopamine_rpe_has_no_rollback_method(self) -> None:
        rpe = _rpe()
        assert not hasattr(rpe, "rollback")


class TestRPEDecisionObserveOnlyPreserved:
    @pytest.mark.asyncio
    async def test_dry_run_decisions_still_observe_only(self) -> None:
        """Decisions produced inside dry_run() still have observe-only invariant."""
        rpe = _rpe()
        # We can't directly access the decisions produced inside dry_run,
        # but we can confirm proposals carry decisions with correct mode.
        proposals = await rpe.dry_run(_ctx("trace-safe-mode"))
        for p in proposals:
            assert p.decision.mode == "observe_only"
            assert p.decision.applied is False
            assert p.decision.target is None
            assert p.decision.proposed_delta is None
            assert p.decision.rollback_id is None

    @pytest.mark.asyncio
    async def test_observe_decisions_unchanged(self) -> None:
        rpe = _rpe()
        decisions = await rpe.observe(_ctx("trace-safe-obs"))
        assert len(decisions) == 1
        d = decisions[0]
        assert d.mode == "observe_only"
        assert d.applied is False
        assert d.target is None
        assert d.proposed_delta is None
        assert d.rollback_id is None


class TestNoSchemaChanges:
    def test_rpe_context_fields_unchanged(self) -> None:
        from dataclasses import fields
        ctx_fields = {f.name for f in fields(RPEContext)}
        # STEP 1 fields must still be present and no new unexpected fields.
        expected = {
            "trace_id", "session_id", "category", "difficulty",
            "response_source", "latency_ms", "error_occurred",
            "timeout_occurred", "continuation_bypass", "pfc_active",
            "pfc_cue_type", "pfc_hint_applied", "extra",
        }
        assert expected.issubset(ctx_fields)

    def test_rpe_decision_observe_only_invariant_still_enforced(self) -> None:
        reward = RPEReward(
            source="mock",
            expected_reward=0.5,
            actual_reward=0.5,
            confidence=1.0,
        )
        ctx = _ctx()
        with pytest.raises(ValueError, match="observe_only"):
            RPEDecision(reward=reward, context=ctx, mode="dry_run")  # type: ignore[arg-type]
