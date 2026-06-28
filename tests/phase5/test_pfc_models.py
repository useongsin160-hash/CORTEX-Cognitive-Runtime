"""Phase 5 STEP 3 — PFC data model unit tests."""
from __future__ import annotations

import pytest

from app.routing.pfc import (
    GoalCandidate,
    GoalSnapshot,
    GoalStackSummary,
    PFCDecision,
    PFCHint,
    SessionGoalContextSummary,
    make_goal_stack_summary,
)
from app.memory.goal import make_goal
from app.memory.session_goal_context import SessionGoalContext


# ---------------------------------------------------------------------------
# GoalSnapshot
# ---------------------------------------------------------------------------


def test_goal_snapshot_is_frozen():
    snap = GoalSnapshot(
        goal_id="g1", title="test", category="coding",
        priority=0.8, source="user_explicit", status="active",
    )
    with pytest.raises((AttributeError, TypeError)):
        snap.title = "changed"  # type: ignore[misc]


def test_goal_snapshot_minimal():
    snap = GoalSnapshot(
        goal_id="g1", title="hello", category=None,
        priority=0.5, source="pfc_inferred", status="active",
    )
    assert snap.summary is None
    assert snap.embedding is None


def test_goal_snapshot_with_embedding():
    emb = (0.1, 0.2, 0.3)
    snap = GoalSnapshot(
        goal_id="g1", title="hello", category="coding",
        priority=0.8, source="user_explicit", status="active",
        embedding=emb,
    )
    assert snap.embedding == emb


# ---------------------------------------------------------------------------
# PFCHint — validation
# ---------------------------------------------------------------------------


def test_pfchint_valid_confidence_zero():
    h = PFCHint(intent="general", cue_type="general_fallback", confidence=0.0)
    assert h.confidence == 0.0


def test_pfchint_valid_confidence_one():
    h = PFCHint(intent="general", cue_type="general_fallback", confidence=1.0)
    assert h.confidence == 1.0


def test_pfchint_confidence_below_zero_raises():
    with pytest.raises(ValueError, match="confidence"):
        PFCHint(intent="general", cue_type="general_fallback", confidence=-0.01)


def test_pfchint_confidence_above_one_raises():
    with pytest.raises(ValueError, match="confidence"):
        PFCHint(intent="general", cue_type="general_fallback", confidence=1.01)


def test_pfchint_frozen():
    h = PFCHint(intent="general", cue_type="general_fallback", confidence=0.5)
    with pytest.raises((AttributeError, TypeError)):
        h.confidence = 0.9  # type: ignore[misc]


def test_pfchint_optional_fields_default_none():
    h = PFCHint(intent="general", cue_type="general_fallback", confidence=0.5)
    assert h.matched_goal_id is None
    assert h.candidate_title is None


def test_pfchint_with_matched_goal():
    h = PFCHint(
        intent="match_active",
        cue_type="active_match",
        confidence=0.7,
        matched_goal_id="g_abc",
    )
    assert h.matched_goal_id == "g_abc"


# ---------------------------------------------------------------------------
# GoalCandidate
# ---------------------------------------------------------------------------


def test_goal_candidate_defaults():
    c = GoalCandidate(title="My new goal")
    assert c.category is None
    assert c.source == "pfc_inferred"
    assert c.priority == 0.5
    assert c.summary is None


def test_goal_candidate_frozen():
    c = GoalCandidate(title="test")
    with pytest.raises((AttributeError, TypeError)):
        c.title = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PFCDecision
# ---------------------------------------------------------------------------


def test_pfc_decision_hint_only():
    hint = PFCHint(intent="general", cue_type="general_fallback", confidence=0.1)
    d = PFCDecision(hint=hint)
    assert d.new_goal_candidate is None
    assert d.matched_goal is None


def test_pfc_decision_with_candidate():
    hint = PFCHint(intent="create_goal", cue_type="goal_creation", confidence=0.8)
    cand = GoalCandidate(title="Build a game")
    d = PFCDecision(hint=hint, new_goal_candidate=cand)
    assert d.new_goal_candidate.title == "Build a game"


def test_pfc_decision_with_matched_goal():
    snap = GoalSnapshot(
        goal_id="g1", title="hello", category=None,
        priority=0.8, source="user_explicit", status="active",
    )
    hint = PFCHint(
        intent="complete_goal", cue_type="completion",
        confidence=0.9, matched_goal_id="g1",
    )
    d = PFCDecision(hint=hint, matched_goal=snap)
    assert d.matched_goal.goal_id == "g1"


def test_pfc_decision_frozen():
    hint = PFCHint(intent="general", cue_type="general_fallback", confidence=0.1)
    d = PFCDecision(hint=hint)
    with pytest.raises((AttributeError, TypeError)):
        d.hint = hint  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GoalStackSummary
# ---------------------------------------------------------------------------


def test_goal_stack_summary_empty():
    s = GoalStackSummary(active_goals=(), top_goal=None, all_goals=(), depth=0)
    assert s.depth == 0
    assert s.top_goal is None


def test_goal_stack_summary_with_goals():
    snap = GoalSnapshot(
        goal_id="g1", title="hello", category=None,
        priority=0.8, source="user_explicit", status="active",
    )
    s = GoalStackSummary(
        active_goals=(snap,), top_goal=snap, all_goals=(snap,), depth=1
    )
    assert s.top_goal.goal_id == "g1"
    assert len(s.active_goals) == 1


def test_goal_stack_summary_frozen():
    s = GoalStackSummary(active_goals=(), top_goal=None, all_goals=(), depth=0)
    with pytest.raises((AttributeError, TypeError)):
        s.depth = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SessionGoalContextSummary
# ---------------------------------------------------------------------------


def test_session_goal_context_summary():
    stack = GoalStackSummary(active_goals=(), top_goal=None, all_goals=(), depth=0)
    s = SessionGoalContextSummary(
        scope_id="sess_1",
        scope_type="session",
        goal_stack_summary=stack,
    )
    assert s.last_active_goal_id is None
    assert s.scope_type == "session"


# ---------------------------------------------------------------------------
# make_goal_stack_summary
# ---------------------------------------------------------------------------


def test_make_goal_stack_summary_empty():
    ctx = SessionGoalContext.for_session("s1")
    summary = make_goal_stack_summary(ctx)
    assert summary.depth == 0
    assert summary.top_goal is None
    assert summary.active_goals == ()
    assert summary.all_goals == ()


def test_make_goal_stack_summary_with_active_goal():
    ctx = SessionGoalContext.for_session("s2")
    g = make_goal(title="code review", source="user_explicit", priority=0.8)
    ctx.add_goal(g)
    summary = make_goal_stack_summary(ctx)
    assert summary.depth == 1
    assert summary.top_goal is not None
    assert summary.top_goal.goal_id == g.goal_id
    assert len(summary.active_goals) == 1


def test_make_goal_stack_summary_with_embedding():
    ctx = SessionGoalContext.for_session("s3")
    g = make_goal(title="test goal", source="user_explicit")
    ctx.add_goal(g)
    emb = (0.1, 0.2, 0.3)
    summary = make_goal_stack_summary(ctx, embeddings={g.goal_id: emb})
    assert summary.top_goal.embedding == emb


def test_make_goal_stack_summary_source_normalization():
    ctx = SessionGoalContext.for_session("s4")
    g = make_goal(title="x", source="pfc_inferred")
    ctx.add_goal(g)
    summary = make_goal_stack_summary(ctx)
    # source should be a plain string
    assert isinstance(summary.all_goals[0].source, str)
    assert summary.all_goals[0].source == "pfc_inferred"
