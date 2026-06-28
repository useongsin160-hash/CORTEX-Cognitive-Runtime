"""Phase 5 STEP 3 — PFC goal_creation cue."""
from __future__ import annotations

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.pfc import GoalStackSummary, PrefrontalCortex


def _eval(category: str = "coding", confidence: float = 0.8) -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category=category, confidence=confidence,
        similarity=0.5, embedding=[],
    )


@pytest.fixture
def pfc() -> PrefrontalCortex:
    return PrefrontalCortex()


# ---------------------------------------------------------------------------
# 생성 신호 → "create_goal"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creation_mokpyo(pfc):
    d = await pfc.infer_hint("새로운 목표 추가해줘", _eval())
    assert d.hint.cue_type == "goal_creation"
    assert d.hint.intent == "create_goal"
    assert d.hint.confidence == 0.8


@pytest.mark.asyncio
async def test_creation_sijakha(pfc):
    d = await pfc.infer_hint("Python 코딩 시작해", _eval(category="coding"))
    assert d.hint.cue_type == "goal_creation"


@pytest.mark.asyncio
async def test_creation_sijakha_ja(pfc):
    d = await pfc.infer_hint("게임 개발 시작하자", _eval(category="game_design"))
    assert d.hint.cue_type == "goal_creation"


@pytest.mark.asyncio
async def test_creation_want_to(pfc):
    d = await pfc.infer_hint("I want to learn Python", _eval())
    assert d.hint.cue_type == "goal_creation"


@pytest.mark.asyncio
async def test_creation_would_like_to(pfc):
    d = await pfc.infer_hint("I would like to build a web app", _eval())
    assert d.hint.cue_type == "goal_creation"


@pytest.mark.asyncio
async def test_creation_goal_keyword(pfc):
    d = await pfc.infer_hint("my main goal is to finish the report", _eval())
    assert d.hint.cue_type == "goal_creation"


@pytest.mark.asyncio
async def test_creation_halyeogo(pfc):
    d = await pfc.infer_hint("알고리즘 구현하려고 해", _eval())
    assert d.hint.cue_type == "goal_creation"


@pytest.mark.asyncio
async def test_creation_wonhae(pfc):
    d = await pfc.infer_hint("더 나은 코드를 원해", _eval())
    assert d.hint.cue_type == "goal_creation"


# ---------------------------------------------------------------------------
# "이제 " alone은 생성 cue 아님
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creation_ijae_alone_no_trigger(pfc):
    """'이제' 단독으로는 goal_creation을 발동하지 않음."""
    d = await pfc.infer_hint("이제", _eval())
    assert d.hint.cue_type != "goal_creation"


@pytest.mark.asyncio
async def test_creation_ijae_space_alone_no_trigger(pfc):
    d = await pfc.infer_hint("이제 ", _eval())
    assert d.hint.cue_type != "goal_creation"


# ---------------------------------------------------------------------------
# candidate_title 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creation_candidate_title_populated(pfc):
    query = "새로운 목표 만들어줘"
    d = await pfc.infer_hint(query, _eval())
    assert d.hint.candidate_title is not None
    assert d.hint.candidate_title == query.strip()[:120]


@pytest.mark.asyncio
async def test_creation_candidate_title_truncated_at_120(pfc):
    long_query = "목표: " + "a" * 200
    d = await pfc.infer_hint(long_query, _eval())
    assert len(d.hint.candidate_title) <= 120


# ---------------------------------------------------------------------------
# new_goal_candidate 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creation_new_goal_candidate_populated(pfc):
    d = await pfc.infer_hint("새로운 목표 만들어줘", _eval(category="coding"))
    assert d.new_goal_candidate is not None
    assert d.new_goal_candidate.source == "pfc_inferred"
    assert d.new_goal_candidate.priority == 0.5


@pytest.mark.asyncio
async def test_creation_candidate_category_from_eval(pfc):
    """non-general category는 candidate에 포함."""
    d = await pfc.infer_hint("시작하자", _eval(category="math_logic"))
    assert d.new_goal_candidate.category == "math_logic"


@pytest.mark.asyncio
async def test_creation_candidate_category_general_becomes_none(pfc):
    """general category → candidate.category=None."""
    d = await pfc.infer_hint("시작하자", _eval(category="general"))
    assert d.new_goal_candidate.category is None
