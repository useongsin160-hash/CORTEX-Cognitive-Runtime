"""B10 — routes signal plumbing into BG/CR (no e5; helpers tested directly).

Covers the routes helpers: _run_pfc_decision (real PFC with goal context),
_pfc_explore_signal (CR explore = uncertain PFC), and _basal_ganglia_apply
filling the BG context with real pfc/lc/rpe signals. Here it runs observe-only
(the state carries no .settings, so bg_apply_enabled falls back to False) — these
tests assert the B10 signal fill, not the C2 apply (covered in the wiring suite).
No fabricated signals: LC surfaces ne_boost as {0,1}, RPE comes from the real counter.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import (
    _basal_ganglia_apply,
    _pfc_explore_signal,
    _run_pfc_decision,
)
from app.api.schemas.context import Difficulty, EvaluationResult, TaskContext
from app.basal_ganglia.advisor import BasalGangliaAdvisor
from app.core.logging import SpinalLogger
from app.memory.store import InMemorySessionGoalStore
from app.routing.pfc import PrefrontalCortex
from app.routing.skip_router import RouteDecision
from app.rpe.recent_counter import RPERecentCounter


def _evaluation(category="writing", difficulty=1):
    return EvaluationResult(
        difficulty=difficulty, category=category, embedding=[],
        confidence=0.8, similarity=0.5,
    )


# ── _run_pfc_decision: real PFC, goal context ───────────────────────────────
@pytest.mark.asyncio
async def test_run_pfc_decision_returns_real_decision():
    state = SimpleNamespace(
        pfc=PrefrontalCortex(), session_goal_store=InMemorySessionGoalStore(),
    )
    tc = TaskContext(
        trace_id="t", prompt="write a short story about cats",
        category="writing", difficulty=Difficulty.EASY,
    )
    decision = await _run_pfc_decision(state, tc, _evaluation(), "sess")
    assert decision is not None
    assert 0.0 <= decision.hint.confidence <= 1.0


@pytest.mark.asyncio
async def test_run_pfc_decision_fail_open_no_pfc():
    state = SimpleNamespace(pfc=None)
    tc = TaskContext(trace_id="t", prompt="x", difficulty=Difficulty.EASY)
    assert await _run_pfc_decision(state, tc, _evaluation(), "sess") is None


# ── _pfc_explore_signal: uncertain PFC = explore ────────────────────────────
def test_explore_signal_true_for_uncertain_fallback():
    d = SimpleNamespace(hint=SimpleNamespace(cue_type="general_fallback", confidence=0.1))
    assert _pfc_explore_signal(d) is True


def test_explore_signal_false_for_confident_cue():
    d = SimpleNamespace(hint=SimpleNamespace(cue_type="completion", confidence=0.9))
    assert _pfc_explore_signal(d) is False


def test_explore_signal_false_for_high_confidence_fallback():
    d = SimpleNamespace(hint=SimpleNamespace(cue_type="category_fallback", confidence=0.6))
    assert _pfc_explore_signal(d) is False


def test_explore_signal_false_for_none():
    assert _pfc_explore_signal(None) is False


# C4 widening: explore now fires for ANY low-confidence cue (not only fallbacks) —
# a borderline goal match scored just over the 0.5 match threshold is uncertain too.
def test_explore_signal_true_for_low_conf_non_fallback_match():
    d = SimpleNamespace(hint=SimpleNamespace(cue_type="active_match", confidence=0.55))
    assert _pfc_explore_signal(d) is True


def test_explore_signal_false_for_confident_match():
    d = SimpleNamespace(hint=SimpleNamespace(cue_type="embedding_match", confidence=0.65))
    assert _pfc_explore_signal(d) is False


# ── _basal_ganglia_apply (observe-only): full signals fill the BG context ───
@pytest.mark.asyncio
async def test_observe_fills_signals_and_stays_applied_false():
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    counter = RPERecentCounter()
    counter.record("sess", "coding", 0.06)  # one real positive
    state = SimpleNamespace(basal_ganglia=advisor, rpe_recent_counter=counter)
    tc = TaskContext(
        trace_id="b10-bg", category="coding", difficulty=Difficulty.VERY_HARD,
        ne_boost=True, synapse_snapshot={"coding": 0.6},
    )
    pfc_decision = SimpleNamespace(
        hint=SimpleNamespace(cue_type="category_fallback", confidence=0.6),
        matched_goal=None,
    )
    await _basal_ganglia_apply(
        state, tc, RouteDecision(path="full_pipeline", reason="b10"), pfc_decision,
        trace_id="b10-bg", session_id="sess",
    )
    events = [
        e for e in logger.get_trace("b10-bg") if e.event_type == "bg.evaluated"
    ]
    assert len(events) == 1
    assert events[0].payload["applied"] is False  # B10 fills input only


@pytest.mark.asyncio
async def test_observe_no_advisor_is_noop():
    await _basal_ganglia_apply(
        SimpleNamespace(), TaskContext(trace_id="t", category="coding"),
        RouteDecision(path="lightweight", reason="b10"), None,
        trace_id="t", session_id="s",
    )  # must not raise
