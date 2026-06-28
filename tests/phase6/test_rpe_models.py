"""Phase 6 STEP 1 — RPE data model tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.rpe.models import RPEContext, RPEDecision, RPEReward


def _ctx(**overrides) -> RPEContext:
    defaults = {"trace_id": "trace-1"}
    defaults.update(overrides)
    return RPEContext(**defaults)


class TestRPEContext:
    def test_default_construction(self) -> None:
        ctx = _ctx()
        assert ctx.trace_id == "trace-1"
        assert ctx.session_id is None
        assert ctx.difficulty == 0
        assert ctx.latency_ms == 0.0
        assert ctx.error_occurred is False
        assert ctx.extra == ()

    def test_frozen(self) -> None:
        ctx = _ctx()
        with pytest.raises(FrozenInstanceError):
            ctx.trace_id = "other"  # type: ignore[misc]

    def test_extra_is_tuple_not_dict(self) -> None:
        ctx = _ctx(extra=(("k1", 1), ("k2", "v")))
        assert ctx.extra == (("k1", 1), ("k2", "v"))
        assert ctx.extra_dict() == {"k1": 1, "k2": "v"}

    def test_extra_dict_input_rejected(self) -> None:
        with pytest.raises(TypeError):
            RPEContext(trace_id="t", extra={"k": "v"})  # type: ignore[arg-type]

    def test_extra_duplicate_key_raises(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            RPEContext(trace_id="t", extra=(("k", 1), ("k", 2)))

    def test_extra_non_scalar_value_raises(self) -> None:
        with pytest.raises(TypeError, match="JSON scalar"):
            RPEContext(trace_id="t", extra=(("k", [1, 2, 3]),))  # type: ignore[arg-type]

    def test_extra_accepts_none_value(self) -> None:
        ctx = _ctx(extra=(("k", None),))
        assert ctx.extra_dict() == {"k": None}

    def test_latency_ms_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="latency_ms"):
            RPEContext(trace_id="t", latency_ms=-1.0)

    def test_difficulty_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="difficulty"):
            RPEContext(trace_id="t", difficulty=-1)

    def test_extra_bad_shape_raises(self) -> None:
        with pytest.raises(TypeError, match="2-tuples"):
            RPEContext(trace_id="t", extra=(("k",),))  # type: ignore[arg-type]


class TestRPEReward:
    def test_basic_construction(self) -> None:
        r = RPEReward(
            source="mock",
            expected_reward=0.5,
            actual_reward=0.5,
            confidence=1.0,
        )
        assert r.prediction_error == 0.0

    def test_prediction_error_positive(self) -> None:
        r = RPEReward(
            source="heuristic",
            expected_reward=0.3,
            actual_reward=0.9,
            confidence=0.3,
        )
        assert r.prediction_error == pytest.approx(0.6)

    def test_prediction_error_negative(self) -> None:
        r = RPEReward(
            source="heuristic",
            expected_reward=0.9,
            actual_reward=0.3,
            confidence=0.3,
        )
        assert r.prediction_error == pytest.approx(-0.6)

    def test_expected_reward_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="expected_reward"):
            RPEReward(
                source="mock",
                expected_reward=1.5,
                actual_reward=0.5,
                confidence=1.0,
            )

    def test_actual_reward_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="actual_reward"):
            RPEReward(
                source="mock",
                expected_reward=0.5,
                actual_reward=-0.1,
                confidence=1.0,
            )

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            RPEReward(
                source="mock",
                expected_reward=0.5,
                actual_reward=0.5,
                confidence=2.0,
            )


def _reward() -> RPEReward:
    return RPEReward(
        source="mock",
        expected_reward=0.5,
        actual_reward=0.5,
        confidence=1.0,
    )


class TestRPEDecision:
    def test_default_observe_only(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx())
        assert d.mode == "observe_only"
        assert d.applied is False
        assert d.target is None
        assert d.proposed_delta is None
        assert d.rollback_id is None
        assert d.max_delta == 0.1
        assert d.trace_id == "trace-1"

    def test_mode_dry_run_rejected(self) -> None:
        with pytest.raises(ValueError, match="observe_only"):
            RPEDecision(reward=_reward(), context=_ctx(), mode="dry_run")  # type: ignore[arg-type]

    def test_mode_active_rejected(self) -> None:
        with pytest.raises(ValueError, match="observe_only"):
            RPEDecision(reward=_reward(), context=_ctx(), mode="active")  # type: ignore[arg-type]

    def test_applied_true_rejected(self) -> None:
        with pytest.raises(ValueError, match="applied"):
            RPEDecision(reward=_reward(), context=_ctx(), applied=True)

    def test_target_set_rejected(self) -> None:
        with pytest.raises(ValueError, match="target"):
            RPEDecision(reward=_reward(), context=_ctx(), target="synapse_weight")

    def test_proposed_delta_rejected(self) -> None:
        with pytest.raises(ValueError, match="proposed_delta"):
            RPEDecision(reward=_reward(), context=_ctx(), proposed_delta=0.05)

    def test_rollback_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="rollback_id"):
            RPEDecision(reward=_reward(), context=_ctx(), rollback_id="rb-1")

    def test_max_delta_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_delta"):
            RPEDecision(reward=_reward(), context=_ctx(), max_delta=-0.1)

    def test_trace_id_default_from_context(self) -> None:
        d = RPEDecision(reward=_reward(), context=_ctx(trace_id="trace-7"))
        assert d.trace_id == "trace-7"

    def test_trace_id_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="trace_id mismatch"):
            RPEDecision(
                reward=_reward(),
                context=_ctx(trace_id="trace-7"),
                trace_id="trace-9",
            )

    def test_session_scope_mismatch_rejected(self) -> None:
        ctx = _ctx(session_id="s-1")
        with pytest.raises(ValueError, match="session_scope"):
            RPEDecision(reward=_reward(), context=ctx, session_scope="s-2")

    def test_session_scope_consistent(self) -> None:
        ctx = _ctx(session_id="s-1")
        d = RPEDecision(reward=_reward(), context=ctx, session_scope="s-1")
        assert d.session_scope == "s-1"
