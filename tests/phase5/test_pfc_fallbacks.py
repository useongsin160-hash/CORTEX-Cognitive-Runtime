"""Phase 5 STEP 3 — PFC category_fallback + general_fallback."""
from __future__ import annotations

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.pfc import GoalStackSummary, PrefrontalCortex

_EMPTY_STACK = GoalStackSummary(active_goals=(), top_goal=None, all_goals=(), depth=0)


def _eval(
    category: str = "coding",
    confidence: float = 0.8,
    embedding: list[float] | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category=category, confidence=confidence,
        similarity=0.5, embedding=embedding or [],
    )


@pytest.fixture
def pfc() -> PrefrontalCortex:
    return PrefrontalCortex()


# ---------------------------------------------------------------------------
# category_fallback — non-"general" category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_fallback_coding(pfc):
    d = await pfc.infer_hint("어떻게 하면 좋을까요", _eval(category="coding"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"
    assert d.hint.intent == "category_hint"


@pytest.mark.asyncio
async def test_category_fallback_math_logic(pfc):
    d = await pfc.infer_hint("help", _eval(category="math_logic"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"


@pytest.mark.asyncio
async def test_category_fallback_writing(pfc):
    d = await pfc.infer_hint("help", _eval(category="writing"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"


@pytest.mark.asyncio
async def test_category_fallback_game_design(pfc):
    d = await pfc.infer_hint("help", _eval(category="game_design"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"


@pytest.mark.asyncio
async def test_category_fallback_data_analysis(pfc):
    d = await pfc.infer_hint("help", _eval(category="data_analysis"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"


@pytest.mark.asyncio
async def test_category_fallback_system_design(pfc):
    d = await pfc.infer_hint("help", _eval(category="system_design"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"


@pytest.mark.asyncio
async def test_category_fallback_confidence_formula(pfc):
    """confidence = eval_result.confidence * 0.6."""
    d = await pfc.infer_hint("help", _eval(category="coding", confidence=0.9), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "category_fallback"
    assert d.hint.confidence == pytest.approx(0.9 * 0.6)


@pytest.mark.asyncio
async def test_category_fallback_confidence_capped_at_one(pfc):
    """confidence * 0.6 은 항상 1.0 이하."""
    d = await pfc.infer_hint("help", _eval(category="coding", confidence=1.0), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.confidence <= 1.0


@pytest.mark.asyncio
async def test_category_fallback_new_goal_candidate_none(pfc):
    d = await pfc.infer_hint("help", _eval(category="coding"), goal_stack_summary=_EMPTY_STACK)
    assert d.new_goal_candidate is None
    assert d.matched_goal is None


# ---------------------------------------------------------------------------
# general_fallback — "general" category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_general_fallback_general_category(pfc):
    d = await pfc.infer_hint("안녕", _eval(category="general"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type == "general_fallback"
    assert d.hint.intent == "general"


@pytest.mark.asyncio
async def test_general_fallback_confidence(pfc):
    d = await pfc.infer_hint("안녕", _eval(category="general"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.confidence == 0.1


@pytest.mark.asyncio
async def test_general_fallback_no_candidates(pfc):
    d = await pfc.infer_hint("안녕", _eval(category="general"), goal_stack_summary=_EMPTY_STACK)
    assert d.new_goal_candidate is None
    assert d.matched_goal is None


@pytest.mark.asyncio
async def test_general_fallback_no_stack_summary(pfc):
    """goal_stack_summary=None이어도 general_fallback 동작."""
    d = await pfc.infer_hint("안녕", _eval(category="general"), goal_stack_summary=None)
    assert d.hint.cue_type == "general_fallback"


# ---------------------------------------------------------------------------
# category_fallback > general_fallback 분기 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_non_general_does_not_reach_general_fallback(pfc):
    d = await pfc.infer_hint("help", _eval(category="coding"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type != "general_fallback"


@pytest.mark.asyncio
async def test_general_category_skips_category_fallback(pfc):
    d = await pfc.infer_hint("안녕", _eval(category="general"), goal_stack_summary=_EMPTY_STACK)
    assert d.hint.cue_type != "category_fallback"


# ---------------------------------------------------------------------------
# 전체 8단계 — no-op 환경에서 general_fallback 도달 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_ladder_reaches_general_fallback(pfc):
    """completion/creation/continuation/correction/match 모두 실패 → general."""
    d = await pfc.infer_hint(
        "안녕하세요",  # No cue keywords
        _eval(category="general", embedding=[]),
        goal_stack_summary=_EMPTY_STACK,
        active_goal=None,
    )
    assert d.hint.cue_type == "general_fallback"
    assert d.hint.intent == "general"
