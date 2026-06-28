"""Phase 5 STEP 4 — PlannerAgent PFC hint 통합 테스트."""
from __future__ import annotations

import pytest

from app.execution.planner_agent import PlannerAgent
from app.routing.pfc import (
    GoalCandidate,
    GoalSnapshot,
    PFCDecision,
    PFCHint,
    PFCIntegrationConfig,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _hint(
    cue_type: str,
    confidence: float = 0.85,
    intent: str = "continue_goal",
    matched_goal_id: str | None = None,
) -> PFCHint:
    return PFCHint(
        intent=intent,
        cue_type=cue_type,
        confidence=confidence,
        matched_goal_id=matched_goal_id,
    )


def _snap(
    goal_id: str = "g1",
    category: str | None = "coding",
    status: str = "active",
) -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id,
        title="Some Goal",
        category=category,
        priority=0.8,
        source="user_explicit",
        status=status,
    )


def _decision(
    cue_type: str,
    confidence: float = 0.85,
    intent: str = "continue_goal",
    matched_goal: "GoalSnapshot | None" = None,
    new_goal_candidate: "GoalCandidate | None" = None,
) -> PFCDecision:
    return PFCDecision(
        hint=_hint(cue_type, confidence, intent),
        matched_goal=matched_goal,
        new_goal_candidate=new_goal_candidate,
    )


@pytest.fixture
def planner() -> PlannerAgent:
    return PlannerAgent()


# ---------------------------------------------------------------------------
# pfc_decision=None → Phase 4 STEP 5.2.5 패턴 동일 동작 (회귀 보장)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_none_returns_regex_intent(planner):
    pre_plan = await planner.create_pre_plan(
        query="implement a sorting function",
        difficulty=1,
        pfc_decision=None,
    )
    assert pre_plan.intent == "code_generation"


@pytest.mark.asyncio
async def test_pfc_none_category_fallback(planner):
    pre_plan = await planner.create_pre_plan(
        query="some generic query",
        difficulty=1,
        category="coding",
        pfc_decision=None,
    )
    assert pre_plan.intent == "code_generation"


@pytest.mark.asyncio
async def test_pfc_none_general_fallback(planner):
    pre_plan = await planner.create_pre_plan(
        query="some generic query",
        difficulty=1,
        pfc_decision=None,
    )
    assert pre_plan.intent == "general"


# ---------------------------------------------------------------------------
# 강한 cue (completion / goal_creation / continuation / correction)
# → PFC intent 강제 채택
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_cue_returns_answer(planner):
    decision = _decision("completion", confidence=0.85, intent="complete_goal")
    pre_plan = await planner.create_pre_plan(
        query="완료했어",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "answer"


@pytest.mark.asyncio
async def test_goal_creation_cue_pfc_intent(planner):
    candidate = GoalCandidate(title="new coding project", category="coding")
    decision = _decision(
        "goal_creation",
        confidence=0.85,
        intent="create_goal",
        new_goal_candidate=candidate,
    )
    pre_plan = await planner.create_pre_plan(
        query="새 코딩 프로젝트 시작할게",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "code_generation"


@pytest.mark.asyncio
async def test_goal_creation_cue_candidate_category_exposed(planner):
    """goal_creation: GoalCandidate.category가 planner intent 결정에 사용됨."""
    candidate = GoalCandidate(title="data project", category="data_analysis")
    decision = _decision(
        "goal_creation",
        confidence=0.9,
        intent="create_goal",
        new_goal_candidate=candidate,
    )
    pre_plan = await planner.create_pre_plan(
        query="data project 시작",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "analysis"


@pytest.mark.asyncio
async def test_continuation_cue_pfc_intent(planner):
    goal = _snap(category="writing")
    decision = _decision(
        "continuation",
        confidence=0.9,
        intent="continue_goal",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="계속 진행해줘",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "creative"


@pytest.mark.asyncio
async def test_correction_cue_pfc_intent(planner):
    goal = _snap(category="coding")
    decision = _decision(
        "correction",
        confidence=0.88,
        intent="correct_goal",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="아니야 틀렸어 수정해줘",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "code_generation"


# ---------------------------------------------------------------------------
# 조건부 cue (active_match / embedding_match) — confidence 분기
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_match_high_confidence_uses_pfc(planner):
    """active_match + confidence 0.8 >= 0.7 → PFC intent 채택."""
    goal = _snap(category="coding")
    decision = _decision(
        "active_match",
        confidence=0.8,
        intent="match_active",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="some query",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "code_generation"


@pytest.mark.asyncio
async def test_active_match_low_confidence_uses_regex(planner):
    """active_match + confidence 0.5 < 0.7 → regex 우선."""
    goal = _snap(category="coding")
    decision = _decision(
        "active_match",
        confidence=0.5,
        intent="match_active",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="analyze this data set",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "analysis"


@pytest.mark.asyncio
async def test_embedding_match_below_threshold_uses_regex(planner):
    """embedding_match + confidence 0.6 (임계값 0.7 미만) → regex 우선."""
    goal = _snap(category="writing")
    decision = _decision(
        "embedding_match",
        confidence=0.6,
        intent="match_embedding",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="write a story",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "creative"


@pytest.mark.asyncio
async def test_embedding_match_above_threshold_uses_pfc(planner):
    """embedding_match + confidence 0.75 >= 0.7 → PFC 우선."""
    goal = _snap(category="math_logic")
    decision = _decision(
        "embedding_match",
        confidence=0.75,
        intent="match_embedding",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="some random query",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "analysis"


# ---------------------------------------------------------------------------
# 폴백 cue (category_fallback / general_fallback) — regex 우선
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_fallback_uses_regex(planner):
    """category_fallback + confidence 0.5 → regex 우선 (PFC 무시)."""
    decision = _decision("category_fallback", confidence=0.5, intent="category_hint")
    pre_plan = await planner.create_pre_plan(
        query="analyze this",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "analysis"


@pytest.mark.asyncio
async def test_general_fallback_uses_regex(planner):
    """general_fallback → PFC 완전 무시."""
    decision = _decision("general_fallback", confidence=0.9, intent="general")
    pre_plan = await planner.create_pre_plan(
        query="implement a function",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "code_generation"


# ---------------------------------------------------------------------------
# PFC planner_hint → outline에 반영
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_cue_outline_has_prefix(planner):
    decision = _decision("completion", confidence=0.9, intent="complete_goal")
    pre_plan = await planner.create_pre_plan(
        query="완료했어",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.steps_outline[0] == "Acknowledge completion"


@pytest.mark.asyncio
async def test_continuation_cue_outline_has_prefix(planner):
    goal = _snap(category="coding")
    decision = _decision(
        "continuation",
        confidence=0.9,
        intent="continue_goal",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="이어서 해줘",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.steps_outline[0] == "Resume previous goal"


@pytest.mark.asyncio
async def test_correction_cue_outline_has_prefix(planner):
    goal = _snap(category="coding")
    decision = _decision(
        "correction",
        confidence=0.9,
        intent="correct_goal",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="수정해줘",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.steps_outline[0] == "Apply correction"


@pytest.mark.asyncio
async def test_active_match_outline_has_prefix(planner):
    goal = _snap(category="coding")
    decision = _decision(
        "active_match",
        confidence=0.9,
        intent="match_active",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="계속 코딩",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.steps_outline[0] == "Align with active goal"


@pytest.mark.asyncio
async def test_pfc_none_no_cue_prefix(planner):
    """pfc_decision=None → cue prefix 없음."""
    pre_plan = await planner.create_pre_plan(
        query="implement sorting",
        difficulty=1,
        pfc_decision=None,
    )
    assert pre_plan.steps_outline[0] != "Acknowledge completion"
    assert pre_plan.steps_outline[0] != "Resume previous goal"


# ---------------------------------------------------------------------------
# 커스텀 threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_threshold_respected():
    """pfc_confidence_threshold=0.9 설정 시 0.85는 regex 우선."""
    cfg = PFCIntegrationConfig(pfc_confidence_threshold=0.9)
    planner = PlannerAgent(pfc_config=cfg)
    goal = _snap(category="coding")
    decision = _decision(
        "active_match",
        confidence=0.85,
        intent="match_active",
        matched_goal=goal,
    )
    pre_plan = await planner.create_pre_plan(
        query="analyze the data",
        difficulty=1,
        pfc_decision=decision,
    )
    assert pre_plan.intent == "analysis"


@pytest.mark.asyncio
async def test_default_pfc_config_used_when_none():
    """pfc_config=None 시 default PFCIntegrationConfig 사용."""
    planner = PlannerAgent(pfc_config=None)
    assert planner._pfc_config.pfc_confidence_threshold == 0.7
