"""Phase 5 STEP 5 — PFC ↔ CueClassifier 통합 회귀 테스트.

PFC가 CueClassifier를 통해 cue 매칭을 위임하고, STEP 3 cue hierarchy
결과가 100% 유지되는지 확인한다.
"""
from __future__ import annotations

import pytest

from app.api.schemas.context import EvaluationResult
from app.routing.cue_classifier import CueClassifier
from app.routing.pfc import GoalSnapshot, PrefrontalCortex


def _eval(category: str = "coding") -> EvaluationResult:
    return EvaluationResult(
        difficulty=2, category=category, confidence=0.8,
        similarity=0.5, embedding=[],
    )


def _snap(goal_id: str = "g1") -> GoalSnapshot:
    return GoalSnapshot(
        goal_id=goal_id, title="active task", category="coding",
        priority=0.8, source="user_explicit", status="active",
    )


# ---------------------------------------------------------------------------
# PFC가 CueClassifier를 사용함
# ---------------------------------------------------------------------------


def test_pfc_default_creates_cue_classifier():
    """pfc가 default 생성자에서 CueClassifier를 보유."""
    pfc = PrefrontalCortex()
    assert pfc._cue_classifier is not None
    assert isinstance(pfc._cue_classifier, CueClassifier)


def test_pfc_accepts_injected_classifier():
    """pfc에 외부 CueClassifier 주입 가능."""
    classifier = CueClassifier()
    pfc = PrefrontalCortex(cue_classifier=classifier)
    assert pfc._cue_classifier is classifier


# ---------------------------------------------------------------------------
# STEP 3 회귀 — 4종 강한 cue 모두 유지
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_cue_still_works():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("완료", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "completion"
    assert d.hint.confidence == 0.9


@pytest.mark.asyncio
async def test_goal_creation_cue_still_works():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("새로운 목표 추가해줘", _eval())
    assert d.hint.cue_type == "goal_creation"
    assert d.new_goal_candidate is not None


@pytest.mark.asyncio
async def test_continuation_cue_still_works():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("계속해", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"
    assert d.hint.confidence == 0.85


@pytest.mark.asyncio
async def test_correction_cue_still_works():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("수정해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "correction"


# ---------------------------------------------------------------------------
# STEP 5 신규 cue 키워드 추가 동작 확인
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_new_keyword_gago():
    """STEP 5 신규 '가자' cue가 PFC에 전달됨."""
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("가자", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_new_keyword_okay():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("오케이", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


@pytest.mark.asyncio
async def test_continuation_new_keyword_daeum_dangae():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("다음 단계로", _eval(), active_goal=_snap())
    assert d.hint.cue_type == "continuation"


# ---------------------------------------------------------------------------
# False-positive 방지 회귀
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wanryo_report_still_false_positive_protected():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("완료 보고서 작성해줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type != "completion"


@pytest.mark.asyncio
async def test_daeum_ju_not_continuation_in_pfc():
    """'다음 주 일정'은 PFC에서도 continuation 아님."""
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("다음 주 일정 알려줘", _eval(), active_goal=_snap())
    assert d.hint.cue_type != "continuation"


@pytest.mark.asyncio
async def test_ijae_alone_not_goal_creation_in_pfc():
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint("이제", _eval())
    assert d.hint.cue_type != "goal_creation"


# ---------------------------------------------------------------------------
# PFC 시그니처 변경 없음 (infer_hint kwargs 동일)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pfc_signature_preserved():
    """infer_hint 시그니처가 query/eval_result/goal_stack_summary/active_goal."""
    pfc = PrefrontalCortex()
    d = await pfc.infer_hint(
        query="hello",
        eval_result=_eval(category="general"),
        goal_stack_summary=None,
        active_goal=None,
    )
    assert d.hint.cue_type == "general_fallback"


# ---------------------------------------------------------------------------
# 중복 cue 상수 제거 검증
# ---------------------------------------------------------------------------


def test_pfc_module_no_legacy_constants():
    """PFC 모듈에서 _COMPLETION_RE, _CREATION_KEYWORDS 등 제거됨."""
    from app.routing import pfc as pfc_module
    # 이전 STEP 3 상수가 PFC 모듈에 더 이상 존재하지 않음
    assert not hasattr(pfc_module, "_COMPLETION_RE")
    assert not hasattr(pfc_module, "_CREATION_KEYWORDS")
    assert not hasattr(pfc_module, "_CONTINUATION_KEYWORDS")
    assert not hasattr(pfc_module, "_CORRECTION_KEYWORDS")
