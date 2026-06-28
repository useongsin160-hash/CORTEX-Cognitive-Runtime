"""Phase 6 STEP 1 — RPE safety slot tests.

The RPEDecision dataclass declares slots for STEP 2+ (dry_run / active):
    - max_delta
    - rollback_id
    - target
    - proposed_delta
    - applied
    - session_scope
    - trace_id

In STEP 1, these slots must exist but be locked to observe-only values.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from app.rpe.models import RPEContext, RPEDecision, RPEReward


def _ctx(**overrides) -> RPEContext:
    defaults = {"trace_id": "trace-safety"}
    defaults.update(overrides)
    return RPEContext(**defaults)


def _reward() -> RPEReward:
    return RPEReward(
        source="mock",
        expected_reward=0.5,
        actual_reward=0.5,
        confidence=1.0,
    )


class TestSafetySlotsExist:
    def test_decision_declares_required_slots(self) -> None:
        slot_names = {f.name for f in fields(RPEDecision)}
        for required in (
            "mode",
            "max_delta",
            "rollback_id",
            "target",
            "proposed_delta",
            "applied",
            "session_scope",
            "trace_id",
            "reward",
            "context",
        ):
            assert required in slot_names, required


class TestObserveOnlyDefaults:
    def test_default_max_delta(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.max_delta == 0.1

    def test_default_rollback_id(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.rollback_id is None

    def test_default_target(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.target is None

    def test_default_proposed_delta(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.proposed_delta is None

    def test_default_applied(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.applied is False

    def test_default_session_scope(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.session_scope is None

    def test_default_mode(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.mode == "observe_only"


class TestStep1LocksOutMutationFields:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"mode": "dry_run"},
            {"mode": "active"},
            {"applied": True},
            {"target": "synapse_weight"},
            {"target": "ifom_ttl"},
            {"target": "pfc_timeout"},
            {"target": "pfc_confidence"},
            {"target": "tier_1_5_threshold"},
            {"target": "epinephrine_threshold"},
            {"proposed_delta": 0.05},
            {"proposed_delta": 0.0},
            {"proposed_delta": -0.05},
            {"rollback_id": "rb-1"},
        ],
    )
    def test_mutation_field_set_is_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            RPEDecision(reward=_reward(), context=_ctx(), **kwargs)


class TestSessionScopeBehavior:
    def test_session_scope_can_be_none_even_when_context_has_session(self) -> None:
        ctx = _ctx(session_id="s-1")
        d = RPEDecision(reward=_reward(), context=ctx)
        assert d.session_scope is None

    def test_session_scope_matching_context_session(self) -> None:
        ctx = _ctx(session_id="s-1")
        d = RPEDecision(reward=_reward(), context=ctx, session_scope="s-1")
        assert d.session_scope == "s-1"

    def test_session_scope_mismatch_is_rejected(self) -> None:
        ctx = _ctx(session_id="s-1")
        with pytest.raises(ValueError, match="session_scope"):
            RPEDecision(reward=_reward(), context=ctx, session_scope="s-2")
