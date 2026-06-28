"""Phase 5 STEP 3 — PFC correction cue."""
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
# 수정 신호 → "correct_goal"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_sujong(pfc):
    d = await pfc.infer_hint("수정해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"
    assert d.hint.intent == "correct_goal"


@pytest.mark.asyncio
async def test_correction_bakkwo(pfc):
    d = await pfc.infer_hint("방향을 바꿔줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_byeongyeong(pfc):
    d = await pfc.infer_hint("변경이 필요해", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_tteullyeosseo(pfc):
    d = await pfc.infer_hint("틀렸어", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_aniya(pfc):
    d = await pfc.infer_hint("아니야 다시 해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_fix(pfc):
    d = await pfc.infer_hint("please fix the issue", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_wrong(pfc):
    d = await pfc.infer_hint("that's wrong", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_mistake(pfc):
    d = await pfc.infer_hint("there's a mistake here", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


@pytest.mark.asyncio
async def test_correction_change(pfc):
    d = await pfc.infer_hint("change the approach", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


# ---------------------------------------------------------------------------
# confidence 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_confidence(pfc):
    d = await pfc.infer_hint("수정해줘", _eval(), active_goal=_snap())
    assert d.hint.confidence == 0.75


# ---------------------------------------------------------------------------
# matched_goal 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_matched_goal(pfc):
    snap = _snap(goal_id="g_corr")
    d = await pfc.infer_hint("수정해줘", _eval(), active_goal=snap)
    assert d.hint.matched_goal_id == "g_corr"
    assert d.matched_goal is not None


# ---------------------------------------------------------------------------
# active_goal 없으면 correction 미발동
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_no_active_goal_no_trigger(pfc):
    d = await pfc.infer_hint("수정해줘", _eval(), active_goal=None)
    assert d.hint.cue_type != "correction"


# ---------------------------------------------------------------------------
# completion/continuation > correction 우선순위
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_loses_to_completion(pfc):
    snap = _snap()
    # "완료" at end → completion wins even if "fix" is also present
    d = await pfc.infer_hint("완료", _eval(), active_goal=snap)
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_correction_loses_to_continuation(pfc):
    snap = _snap()
    # "계속" triggers continuation before correction in the ladder
    d = await pfc.infer_hint("계속 수정해줘", _eval(), active_goal=snap)
    assert d.hint.cue_type == "continuation"
