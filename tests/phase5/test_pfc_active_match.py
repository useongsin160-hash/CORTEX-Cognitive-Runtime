"""Phase 5 STEP 3 — PFC active_match (composite scoring)."""
from __future__ import annotations

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.pfc import GoalSnapshot, GoalStackSummary, PrefrontalCortex


def _eval(
    category: str = "coding",
    confidence: float = 0.8,
    embedding: list[float] | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category=category, confidence=confidence,
        similarity=0.5, embedding=embedding or [],
    )


def _snap(
    goal_id: str = "g1",
    title: str = "coding project",
    category: str | None = "coding",
    source: str = "user_explicit",
    status: str = "active",
    embedding: tuple[float, ...] | None = None,
) -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id, title=title, category=category,
        priority=0.8, source=source, status=status, embedding=embedding,
    )


def _stack(*goals: GoalSnapshot, all_goals: tuple[GoalSnapshot, ...] | None = None) -> GoalStackSummary:
    active = tuple(g for g in goals if g.status == "active")
    all_ = all_goals if all_goals is not None else goals
    return GoalStackSummary(
        active_goals=active,
        top_goal=active[0] if active else None,
        all_goals=all_,
        depth=len(all_),
    )


@pytest.fixture
def pfc() -> PrefrontalCortex:
    return PrefrontalCortex()


# ---------------------------------------------------------------------------
# 매칭 성공
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_match_category_hit(pfc):
    """같은 category + keyword overlap → active_match."""
    goal = _snap(goal_id="g_code", title="Python coding task", category="coding")
    summary = _stack(goal)
    # query with keyword "Python" and same category
    d = await pfc.infer_hint(
        "Python coding help",
        _eval(category="coding"),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "active_match"
    assert d.hint.matched_goal_id == "g_code"
    assert d.matched_goal is not None


@pytest.mark.asyncio
async def test_active_match_best_score_selected(pfc):
    """여러 active goal 중 최고 점수 goal 선택."""
    g_low = _snap(goal_id="g_low", title="unrelated task", category="writing")
    g_high = _snap(goal_id="g_high", title="coding review", category="coding")
    summary = _stack(g_low, g_high)
    d = await pfc.infer_hint(
        "coding review needed",
        _eval(category="coding"),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "active_match"
    assert d.hint.matched_goal_id == "g_high"


@pytest.mark.asyncio
async def test_active_match_with_embedding(pfc):
    """embedding 있을 때 가중치 적용 → 높은 cosine goal 선택."""
    emb = (1.0, 0.0, 0.0)
    goal = _snap(goal_id="g_emb", title="embedding task", category="coding", embedding=emb)
    summary = _stack(goal)
    d = await pfc.infer_hint(
        "embed task",
        _eval(category="coding", embedding=[1.0, 0.0, 0.0]),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "active_match"


@pytest.mark.asyncio
async def test_active_match_user_explicit_bonus(pfc):
    """user_explicit source는 bonus 가산."""
    g_user = _snap(goal_id="g_u", title="fix bug", category="coding", source="user_explicit")
    g_sys = _snap(goal_id="g_s", title="fix bug", category="coding", source="system")
    summary = _stack(g_user, g_sys)
    d = await pfc.infer_hint(
        "fix the bug",
        _eval(category="coding"),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "active_match"
    assert d.hint.matched_goal_id == "g_u"


# ---------------------------------------------------------------------------
# 매칭 실패 (fallthrough)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_match_no_active_goals_skipped(pfc):
    """active goal 없으면 active_match 건너뜀."""
    completed = _snap(goal_id="g_c", title="done task", status="completed")
    summary = GoalStackSummary(
        active_goals=(),
        top_goal=None,
        all_goals=(completed,),
        depth=1,
    )
    d = await pfc.infer_hint(
        "help me with done task",
        _eval(),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type != "active_match"


@pytest.mark.asyncio
async def test_active_match_below_threshold_fallthrough(pfc):
    """composite score가 threshold 미만 → fallthrough."""
    # Completely unrelated query and goal
    goal = _snap(goal_id="g_unrelated", title="데이터 분석 보고서", category="data_analysis")
    summary = _stack(goal)
    d = await pfc.infer_hint(
        "hello world",
        _eval(category="coding"),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type != "active_match"


@pytest.mark.asyncio
async def test_active_match_no_stack_summary_skipped(pfc):
    """goal_stack_summary=None이면 active_match 건너뜀."""
    d = await pfc.infer_hint(
        "coding review help",
        _eval(category="coding"),
        goal_stack_summary=None,
    )
    assert d.hint.cue_type != "active_match"


# ---------------------------------------------------------------------------
# confidence 범위 검증
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_match_confidence_capped_at_one(pfc):
    """composite score > 1.0이어도 confidence는 1.0으로 clamped."""
    emb = (1.0, 0.0, 0.0)
    goal = _snap(
        goal_id="g_perfect", title="coding coding coding",
        category="coding", source="user_explicit", embedding=emb,
    )
    summary = _stack(goal)
    d = await pfc.infer_hint(
        "coding coding coding",
        _eval(category="coding", embedding=[1.0, 0.0, 0.0]),
        goal_stack_summary=summary,
    )
    if d.hint.cue_type == "active_match":
        assert d.hint.confidence <= 1.0


# ---------------------------------------------------------------------------
# 우선순위: completion/creation/continuation/correction > active_match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_match_loses_to_creation(pfc):
    """goal_creation이 active_match보다 우선."""
    goal = _snap(goal_id="g1", title="coding review", category="coding")
    summary = _stack(goal)
    d = await pfc.infer_hint(
        "새로운 목표 만들어줘",
        _eval(category="coding"),
        goal_stack_summary=summary,
    )
    assert d.hint.cue_type == "goal_creation"
