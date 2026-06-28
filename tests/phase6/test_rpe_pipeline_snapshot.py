"""Phase 6 STEP 3.2 — RPEPipelineSnapshot model tests."""

from __future__ import annotations

import dataclasses

import pytest

from app.rpe.models import RPEContext, RPEPipelineSnapshot


def _snapshot(**kwargs) -> RPEPipelineSnapshot:
    defaults: dict = {
        "trace_id": "trace-snap-001",
        "session_id": "sess-snap-001",
        "category": "coding",
        "difficulty": 2,
        "response_source": "swarm",
        "latency_ms": 42.5,
        "error_occurred": False,
        "timeout_occurred": False,
        "continuation_bypass": False,
        "pfc_active": False,
        "pfc_cue_type": None,
        "pfc_hint_applied": False,
    }
    defaults.update(kwargs)
    return RPEPipelineSnapshot(**defaults)


class TestSnapshotFields:
    def test_all_fields_stored_correctly(self) -> None:
        snap = _snapshot()
        assert snap.trace_id == "trace-snap-001"
        assert snap.session_id == "sess-snap-001"
        assert snap.category == "coding"
        assert snap.difficulty == 2
        assert snap.response_source == "swarm"
        assert snap.latency_ms == pytest.approx(42.5)
        assert snap.error_occurred is False
        assert snap.timeout_occurred is False
        assert snap.continuation_bypass is False
        assert snap.pfc_active is False
        assert snap.pfc_cue_type is None
        assert snap.pfc_hint_applied is False

    def test_snapshot_is_frozen(self) -> None:
        snap = _snapshot()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            snap.trace_id = "modified"  # type: ignore[misc]

    def test_category_none_allowed(self) -> None:
        snap = _snapshot(category=None)
        assert snap.category is None

    def test_difficulty_zero_allowed(self) -> None:
        snap = _snapshot(difficulty=0)
        assert snap.difficulty == 0

    def test_negative_latency_raises(self) -> None:
        with pytest.raises(ValueError, match="latency_ms"):
            _snapshot(latency_ms=-1.0)

    def test_negative_difficulty_raises(self) -> None:
        with pytest.raises(ValueError, match="difficulty"):
            _snapshot(difficulty=-1)

    def test_zero_latency_allowed(self) -> None:
        snap = _snapshot(latency_ms=0.0)
        assert snap.latency_ms == 0.0

    def test_error_flags_independent(self) -> None:
        snap = _snapshot(error_occurred=True, timeout_occurred=False)
        assert snap.error_occurred is True
        assert snap.timeout_occurred is False

    def test_pfc_fields_defaults(self) -> None:
        snap = _snapshot()
        assert snap.pfc_active is False
        assert snap.pfc_cue_type is None
        assert snap.pfc_hint_applied is False

    def test_pfc_active_true(self) -> None:
        snap = _snapshot(pfc_active=True, pfc_cue_type="task_switch", pfc_hint_applied=True)
        assert snap.pfc_active is True
        assert snap.pfc_cue_type == "task_switch"
        assert snap.pfc_hint_applied is True


class TestToRpeContext:
    def test_to_rpe_context_returns_rpe_context(self) -> None:
        snap = _snapshot()
        ctx = snap.to_rpe_context()
        assert isinstance(ctx, RPEContext)

    def test_trace_id_mapped(self) -> None:
        snap = _snapshot(trace_id="t-snap-ctx")
        ctx = snap.to_rpe_context()
        assert ctx.trace_id == "t-snap-ctx"

    def test_session_id_mapped(self) -> None:
        snap = _snapshot(session_id="sess-snap-ctx")
        ctx = snap.to_rpe_context()
        assert ctx.session_id == "sess-snap-ctx"

    def test_category_mapped(self) -> None:
        snap = _snapshot(category="math_logic")
        ctx = snap.to_rpe_context()
        assert ctx.category == "math_logic"

    def test_category_none_preserved(self) -> None:
        snap = _snapshot(category=None)
        ctx = snap.to_rpe_context()
        assert ctx.category is None

    def test_difficulty_mapped(self) -> None:
        snap = _snapshot(difficulty=3)
        ctx = snap.to_rpe_context()
        assert ctx.difficulty == 3

    def test_response_source_mapped(self) -> None:
        snap = _snapshot(response_source="swarm")
        ctx = snap.to_rpe_context()
        assert ctx.response_source == "swarm"

    def test_latency_ms_mapped(self) -> None:
        snap = _snapshot(latency_ms=99.9)
        ctx = snap.to_rpe_context()
        assert ctx.latency_ms == pytest.approx(99.9)

    def test_error_occurred_mapped(self) -> None:
        snap = _snapshot(error_occurred=True)
        ctx = snap.to_rpe_context()
        assert ctx.error_occurred is True

    def test_timeout_occurred_mapped(self) -> None:
        snap = _snapshot(timeout_occurred=True)
        ctx = snap.to_rpe_context()
        assert ctx.timeout_occurred is True

    def test_continuation_bypass_mapped(self) -> None:
        snap = _snapshot(continuation_bypass=True)
        ctx = snap.to_rpe_context()
        assert ctx.continuation_bypass is True

    def test_pfc_fields_mapped(self) -> None:
        snap = _snapshot(pfc_active=True, pfc_cue_type="goal_keep", pfc_hint_applied=True)
        ctx = snap.to_rpe_context()
        assert ctx.pfc_active is True
        assert ctx.pfc_cue_type == "goal_keep"
        assert ctx.pfc_hint_applied is True

    def test_context_is_frozen(self) -> None:
        snap = _snapshot()
        ctx = snap.to_rpe_context()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ctx.trace_id = "modified"  # type: ignore[misc]
