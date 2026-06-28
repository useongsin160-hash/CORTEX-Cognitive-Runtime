"""Phase 6 STEP 5.1 — BasalGangliaAdvisor evaluate() tests."""
from __future__ import annotations

import asyncio

import pytest

from app.basal_ganglia.advisor import BasalGangliaAdvisor
from app.basal_ganglia.models import (
    ActionCandidate,
    ActionSelectionContext,
    ActionSelectionDecision,
    ActionSelectionPolicyConfig,
)
from app.basal_ganglia.policies import ActionSelectionPolicy
from app.core.logging import get_spinal_logger


def _ctx(**overrides) -> ActionSelectionContext:
    defaults = dict(
        trace_id="tr-advisor",
        session_id="s",
        category="coding",
        difficulty=1,
        pfc_active=False,
        pfc_cue_type=None,
        pfc_confidence=0.6,
        pfc_intent_category=None,
        lc_ne_level=0.2,
        lc_intent_label=None,
    )
    defaults.update(overrides)
    return ActionSelectionContext(**defaults)


def _explicit_candidates() -> tuple[ActionCandidate, ...]:
    return (
        ActionCandidate(
            candidate_id="explicit_full",
            candidate_type="swarm_full",
            target_category="coding",
            synapse_weight=0.8,
        ),
        ActionCandidate(
            candidate_id="explicit_fallback",
            candidate_type="fallback",
            target_category="coding",
            synapse_weight=0.1,
        ),
    )


# ---------------------------------------------------------------------------
# Evaluate with explicit candidates
# ---------------------------------------------------------------------------


def test_evaluate_with_explicit_candidates():
    advisor = BasalGangliaAdvisor()
    ctx = _ctx(trace_id="tr-explicit")
    candidates = _explicit_candidates()
    decision = asyncio.run(advisor.evaluate(ctx, candidates))
    assert isinstance(decision, ActionSelectionDecision)
    assert decision.candidates == candidates
    assert decision.selected is not None
    # ctx is difficulty 1 (lightweight anchor 1/3) with mild signals → low demand,
    # so between {swarm_full, fallback} the lighter fallback is the closer match.
    assert decision.selected.candidate_id == "explicit_fallback"


def test_evaluate_applied_always_false():
    advisor = BasalGangliaAdvisor()
    ctx = _ctx(trace_id="tr-applied")
    decision = asyncio.run(advisor.evaluate(ctx, _explicit_candidates()))
    assert decision.applied is False


def test_evaluate_decision_context_matches():
    advisor = BasalGangliaAdvisor()
    ctx = _ctx(trace_id="tr-ctx-match")
    decision = asyncio.run(advisor.evaluate(ctx))
    assert decision.context is ctx


# ---------------------------------------------------------------------------
# Evaluate with default candidates
# ---------------------------------------------------------------------------


def test_evaluate_with_default_candidates():
    advisor = BasalGangliaAdvisor()
    ctx = _ctx(trace_id="tr-default")
    decision = asyncio.run(advisor.evaluate(ctx))
    assert len(decision.candidates) == 4
    types = {c.candidate_type for c in decision.candidates}
    assert types == {"swarm_full", "swarm_minimal", "tier_1_5_augment", "fallback"}


def test_default_candidates_use_context_signals():
    advisor = BasalGangliaAdvisor()
    ctx = _ctx(
        trace_id="tr-signals",
        pfc_confidence=0.55,
        lc_ne_level=0.4,
        synapse_weights=(("coding", 0.66),),
        rpe_recent_positive_count=3,
        rpe_recent_negative_count=1,
    )
    decision = asyncio.run(advisor.evaluate(ctx))
    for cand in decision.candidates:
        assert cand.pfc_confidence == 0.55
        assert cand.lc_ne_level == 0.4
        assert cand.synapse_weight == 0.66
        assert cand.rpe_recent_positive_count == 3
        assert cand.rpe_recent_negative_count == 1


def test_default_candidates_synapse_weight_none_when_no_category_match():
    advisor = BasalGangliaAdvisor()
    ctx = _ctx(
        trace_id="tr-no-match",
        category="writing",
        synapse_weights=(("coding", 0.66),),
    )
    decision = asyncio.run(advisor.evaluate(ctx))
    for cand in decision.candidates:
        assert cand.synapse_weight is None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_bg_evaluated_logged():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "tr-log-eval"
    ctx = _ctx(trace_id=trace_id)
    asyncio.run(advisor.evaluate(ctx))
    events = logger.get_trace(trace_id)
    bg_events = [e for e in events if e.event_type == "bg.evaluated"]
    assert len(bg_events) == 1
    payload = bg_events[0].payload
    assert payload["applied"] is False
    assert payload["category"] == "coding"
    assert "candidates_count" in payload
    assert "selected_id" in payload
    assert "confidence" in payload
    assert "reason" in payload


def test_no_log_when_no_logger():
    advisor = BasalGangliaAdvisor()  # no logger
    ctx = _ctx(trace_id="tr-no-log")
    # Should not raise even without logger
    decision = asyncio.run(advisor.evaluate(ctx))
    assert isinstance(decision, ActionSelectionDecision)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class _RaisingPolicy(ActionSelectionPolicy):
    def select(self, context, candidates):
        raise RuntimeError("boom")


def test_general_error_returns_failsafe_decision():
    advisor = BasalGangliaAdvisor(policy=_RaisingPolicy())
    ctx = _ctx(trace_id="tr-error")
    decision = asyncio.run(advisor.evaluate(ctx))
    assert isinstance(decision, ActionSelectionDecision)
    assert decision.selected is None
    assert decision.candidates == ()
    assert decision.confidence == 0.0
    assert decision.reason == "error"
    assert decision.applied is False


def test_bg_error_logged():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(policy=_RaisingPolicy(), logger=logger)
    trace_id = "tr-error-log"
    ctx = _ctx(trace_id=trace_id)
    asyncio.run(advisor.evaluate(ctx))
    events = logger.get_trace(trace_id)
    err_events = [e for e in events if e.event_type == "bg.error"]
    assert len(err_events) == 1
    payload = err_events[0].payload
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == "boom"
    assert payload["applied"] is False


class _CancelPolicy(ActionSelectionPolicy):
    def select(self, context, candidates):
        raise asyncio.CancelledError("cancelled")


def test_cancelled_error_re_raised():
    advisor = BasalGangliaAdvisor(policy=_CancelPolicy())
    ctx = _ctx(trace_id="tr-cancel")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(advisor.evaluate(ctx))


# ---------------------------------------------------------------------------
# Logger fail-open
# ---------------------------------------------------------------------------


class _FailingLogger:
    """A logger whose log_event raises Exception every time."""

    async def log_event(self, *args, **kwargs):
        raise RuntimeError("logger down")


def test_logger_failure_fail_open():
    advisor = BasalGangliaAdvisor(logger=_FailingLogger())  # type: ignore[arg-type]
    ctx = _ctx(trace_id="tr-logger-fail")
    # Must not raise despite logger failure
    decision = asyncio.run(advisor.evaluate(ctx))
    assert isinstance(decision, ActionSelectionDecision)
    assert decision.applied is False


# ---------------------------------------------------------------------------
# Policy injection
# ---------------------------------------------------------------------------


def test_advisor_uses_injected_policy():
    custom_cfg = ActionSelectionPolicyConfig(ne_demand_factor=0.30)
    custom_policy = ActionSelectionPolicy(config=custom_cfg)
    advisor = BasalGangliaAdvisor(policy=custom_policy)
    assert advisor.policy is custom_policy
