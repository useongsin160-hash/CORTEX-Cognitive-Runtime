"""Phase 5 STEP 3 — PFC embedding_match (cosine-only on all goals)."""
from __future__ import annotations

import math

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.pfc import GoalSnapshot, GoalStackSummary, PrefrontalCortex, _cosine


def _eval(
    category: str = "general",
    embedding: list[float] | None = None,
    confidence: float = 0.8,
) -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category=category, confidence=confidence,
        similarity=0.5, embedding=embedding or [],
    )


def _snap(
    goal_id: str = "g1",
    title: str = "test goal",
    category: str | None = "coding",
    status: str = "active",
    embedding: tuple[float, ...] | None = None,
) -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id, title=title, category=category,
        priority=0.8, source="user_explicit", status=status,
        embedding=embedding,
    )


@pytest.fixture
def pfc() -> PrefrontalCortex:
    return PrefrontalCortex()


# ---------------------------------------------------------------------------
# _cosine 헬퍼 직접 테스트
# ---------------------------------------------------------------------------


def test_cosine_identical():
    assert _cosine([1.0, 0.0, 0.0], (1.0, 0.0, 0.0)) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert _cosine([1.0, 0.0], (0.0, 1.0)) == pytest.approx(0.0)


def test_cosine_negative_clamped_to_zero():
    """음수 cosine은 0.0으로 clamped."""
    result = _cosine([1.0, 0.0], (-1.0, 0.0))
    assert result == pytest.approx(0.0)


def test_cosine_empty_a_returns_zero():
    assert _cosine([], (1.0, 0.0)) == 0.0


def test_cosine_empty_b_returns_zero():
    assert _cosine([1.0, 0.0], ()) == 0.0


def test_cosine_zero_vector_a_returns_zero():
    assert _cosine([0.0, 0.0], (1.0, 1.0)) == 0.0


def test_cosine_similar_vectors():
    a = [0.6, 0.8]
    b = (0.8, 0.6)
    expected = (0.6 * 0.8 + 0.8 * 0.6) / (1.0 * 1.0)  # both unit vectors
    assert _cosine(a, b) == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# embedding_match 발동
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_match_identical_vectors(pfc):
    """identical embedding → cosine 1.0 → embedding_match."""
    emb = [1.0, 0.0, 0.0]
    goal = _snap(goal_id="g_emb", embedding=(1.0, 0.0, 0.0), status="active")
    summary = GoalStackSummary(
        active_goals=(),  # empty active → skip active_match
        top_goal=None,
        all_goals=(goal,),
        depth=1,
    )
    d = await pfc.infer_hint(
        "some query",
        _eval(embedding=emb),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "embedding_match"
    assert d.hint.matched_goal_id == "g_emb"


@pytest.mark.asyncio
async def test_embedding_match_scans_all_statuses(pfc):
    """embedding_match는 active가 아닌 goal도 포함한 ALL goals 스캔."""
    emb = [1.0, 0.0, 0.0]
    paused_goal = _snap(goal_id="g_paused", embedding=(1.0, 0.0, 0.0), status="paused")
    summary = GoalStackSummary(
        active_goals=(),
        top_goal=None,
        all_goals=(paused_goal,),
        depth=1,
    )
    d = await pfc.infer_hint(
        "query text",
        _eval(embedding=emb),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "embedding_match"
    assert d.hint.matched_goal_id == "g_paused"


@pytest.mark.asyncio
async def test_embedding_match_expired_goal_also_scannable(pfc):
    emb = [0.0, 1.0, 0.0]
    expired_goal = _snap(goal_id="g_exp", embedding=(0.0, 1.0, 0.0), status="expired")
    summary = GoalStackSummary(
        active_goals=(),
        top_goal=None,
        all_goals=(expired_goal,),
        depth=1,
    )
    d = await pfc.infer_hint(
        "query",
        _eval(embedding=emb),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "embedding_match"


@pytest.mark.asyncio
async def test_embedding_match_selects_best_cosine(pfc):
    """여러 goal 중 cosine 가장 높은 것 선택."""
    q_emb = [1.0, 0.0, 0.0]
    g_high = _snap(goal_id="g_high", embedding=(0.9, 0.1, 0.0), status="paused")
    g_low = _snap(goal_id="g_low", embedding=(0.0, 1.0, 0.0), status="paused")
    summary = GoalStackSummary(
        active_goals=(),
        top_goal=None,
        all_goals=(g_high, g_low),
        depth=2,
    )
    d = await pfc.infer_hint("q", _eval(embedding=q_emb), goal_stack_summary=summary)
    assert d.hint.cue_type == "embedding_match"
    assert d.hint.matched_goal_id == "g_high"


# ---------------------------------------------------------------------------
# embedding_match 미발동
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_match_no_embedding_in_eval_skipped(pfc):
    """eval_result.embedding이 비어있으면 embedding_match 건너뜀."""
    goal = _snap(goal_id="g1", embedding=(1.0, 0.0, 0.0), status="paused")
    summary = GoalStackSummary(
        active_goals=(), top_goal=None, all_goals=(goal,), depth=1
    )
    d = await pfc.infer_hint(
        "query", _eval(embedding=[]),  # empty embedding
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type != "embedding_match"


@pytest.mark.asyncio
async def test_embedding_match_goal_without_embedding_skipped(pfc):
    """goal.embedding=None이면 해당 goal은 건너뜀."""
    goal_no_emb = _snap(goal_id="g_noemb", embedding=None, status="paused")
    summary = GoalStackSummary(
        active_goals=(), top_goal=None, all_goals=(goal_no_emb,), depth=1
    )
    d = await pfc.infer_hint(
        "query", _eval(embedding=[1.0, 0.0, 0.0]),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type != "embedding_match"


@pytest.mark.asyncio
async def test_embedding_match_orthogonal_below_threshold(pfc):
    """직교 벡터 → cosine=0 → threshold 미달 → fallthrough."""
    goal = _snap(goal_id="g_orth", embedding=(0.0, 1.0, 0.0), status="paused")
    summary = GoalStackSummary(
        active_goals=(), top_goal=None, all_goals=(goal,), depth=1
    )
    d = await pfc.infer_hint(
        "query",
        _eval(embedding=[1.0, 0.0, 0.0]),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type != "embedding_match"


@pytest.mark.asyncio
async def test_embedding_match_confidence_clamped(pfc):
    emb = [1.0, 0.0]
    goal = _snap(goal_id="g1", embedding=(1.0, 0.0), status="paused")
    summary = GoalStackSummary(
        active_goals=(), top_goal=None, all_goals=(goal,), depth=1
    )
    d = await pfc.infer_hint("q", _eval(embedding=emb), goal_stack_summary=summary)
    assert d.hint.cue_type == "embedding_match"
    assert 0.0 <= d.hint.confidence <= 1.0
