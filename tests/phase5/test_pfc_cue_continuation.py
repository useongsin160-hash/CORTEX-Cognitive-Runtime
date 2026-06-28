"""Phase 5 STEP 3 — PFC continuation cue."""
from __future__ import annotations

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.pfc import GoalSnapshot, PrefrontalCortex


def _eval() -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category="coding", confidence=0.8,
        similarity=0.5, embedding=[],
    )


def _snap(goal_id: str = "g1") -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id, title="active task", category="coding",
        priority=0.8, source="user_explicit", status="active",
    )


@pytest.fixture
def pfc() -> PrefrontalCortex:
    return PrefrontalCortex()


# ---------------------------------------------------------------------------
# 계속 신호 → "continue_goal"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_gyesok(pfc):
    d = await pfc.infer_hint("계속해", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"
    assert d.hint.intent == "continue_goal"


@pytest.mark.asyncio
async def test_continuation_iyeoseo(pfc):
    d = await pfc.infer_hint("이어서 해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_gyesok_alone(pfc):
    d = await pfc.infer_hint("계속", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_daeum(pfc):
    d = await pfc.infer_hint("다음 단계로 가줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_continue_english(pfc):
    d = await pfc.infer_hint("please continue", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_next_english(pfc):
    d = await pfc.infer_hint("next step please", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_keep_going(pfc):
    d = await pfc.infer_hint("keep going with it", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


# ---------------------------------------------------------------------------
# confidence 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_confidence(pfc):
    d = await pfc.infer_hint("계속해", _eval(), active_goal=_snap())
    assert d.hint.confidence == 0.85


# ---------------------------------------------------------------------------
# matched_goal 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_matched_goal(pfc):
    snap = _snap(goal_id="g_continue")
    d = await pfc.infer_hint("계속", _eval(), active_goal=snap)
    assert d.hint.matched_goal_id == "g_continue"
    assert d.matched_goal is not None
    assert d.matched_goal.goal_id == "g_continue"


# ---------------------------------------------------------------------------
# active_goal 없으면 continuation 미발동
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_no_active_goal_no_trigger(pfc):
    """active_goal=None이면 continuation 미발동."""
    d = await pfc.infer_hint("계속해", _eval(), active_goal=None)
    assert d.hint.cue_type != "continuation"


# ---------------------------------------------------------------------------
# completion > continuation 우선순위
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_loses_to_completion(pfc):
    """completion이 먼저 — 완료 + 계속 키워드 조합 시 completion 우선."""
    snap = _snap()
    d = await pfc.infer_hint("끝났어", _eval(), active_goal=snap)
    # "끝났어" is completion, not continuation
    assert d.hint.cue_type == "completion"
