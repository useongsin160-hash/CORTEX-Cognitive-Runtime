"""Phase 5 STEP 3 — PFC completion cue + false-positive prevention."""
from __future__ import annotations

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.pfc import GoalSnapshot, GoalStackSummary, PrefrontalCortex


def _eval(category: str = "coding", confidence: float = 0.8) -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category=category, confidence=confidence,
        similarity=0.5, embedding=[],
    )


def _snap(goal_id: str = "g1", title: str = "current goal") -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id, title=title, category="coding",
        priority=0.8, source="user_explicit", status="active",
    )


@pytest.fixture
def pfc() -> PrefrontalCortex:
    return PrefrontalCortex()


# ---------------------------------------------------------------------------
# 완료 신호 → "complete_goal"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_wanryo(pfc):
    d = await pfc.infer_hint("완료", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"
    assert d.hint.intent == "complete_goal"
    assert d.hint.confidence == 0.9


@pytest.mark.asyncio
async def test_completion_wanryo_exclamation(pfc):
    d = await pfc.infer_hint("완료!", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_wanryo_haesseo(pfc):
    d = await pfc.infer_hint("완료했어", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_wanryo_dwaesseo(pfc):
    d = await pfc.infer_hint("완료됐어", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_kkeutnaesseo(pfc):
    d = await pfc.infer_hint("끝났어", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_da_haesseo(pfc):
    d = await pfc.infer_hint("다 했어", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_machyeosseo(pfc):
    d = await pfc.infer_hint("마쳤어", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_done(pfc):
    d = await pfc.infer_hint("done", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_finished(pfc):
    d = await pfc.infer_hint("finished", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


@pytest.mark.asyncio
async def test_completion_completed(pfc):
    d = await pfc.infer_hint("completed", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"


# ---------------------------------------------------------------------------
# 완료 신호 — matched_goal 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_matched_goal_populated(pfc):
    snap = _snap(goal_id="g_xyz", title="active task")
    d = await pfc.infer_hint("완료", _eval(), active_goal=snap)
    assert d.hint.matched_goal_id == "g_xyz"
    assert d.matched_goal is not None
    assert d.matched_goal.goal_id == "g_xyz"


# ---------------------------------------------------------------------------
# False-positive prevention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_false_positive_report(pfc):
    """'완료 보고서 작성해줘' — 완료 보고서를 쓰는 작업, 완료 신호 아님."""
    d = await pfc.infer_hint("완료 보고서 작성해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type != "completion"


@pytest.mark.asyncio
async def test_completion_false_positive_status_check(pfc):
    """'완료 상태 확인해줘' — 상태 확인 요청, 완료 신호 아님."""
    d = await pfc.infer_hint("완료 상태 확인해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type != "completion"


@pytest.mark.asyncio
async def test_completion_false_positive_definition(pfc):
    """'완료 기준 알려줘' — 완료 기준 질문, 완료 신호 아님."""
    d = await pfc.infer_hint("완료 기준 알려줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type != "completion"


# ---------------------------------------------------------------------------
# active_goal 없으면 completion 미발동
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_no_active_goal_no_trigger(pfc):
    """active_goal=None이면 완료 신호가 있어도 completion 미발동."""
    d = await pfc.infer_hint("완료", _eval(), active_goal=None)
    assert d.hint.cue_type != "completion"


@pytest.mark.asyncio
async def test_completion_priority_over_other_steps(pfc):
    """completion은 Step 1 — goal_creation 키워드보다 우선."""
    snap = _snap()
    # query has both "완료" (completion) and "목표" (creation) but completion wins
    d = await pfc.infer_hint("목표 완료", _eval(), active_goal=snap)
    # "완료" is at end → completion
    assert d.hint.cue_type == "completion"
