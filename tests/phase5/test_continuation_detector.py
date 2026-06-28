"""Phase 5 STEP 5 — ContinuationDetector 단위 테스트."""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import ContinuationContext
from app.memory.goal import make_goal
from app.memory.store import InMemorySessionGoalStore
from app.routing.continuation_detector import (
    ContinuationDecision,
    ContinuationDetector,
)
from app.routing.cue_classifier import CueClassifier


# ---------------------------------------------------------------------------
# 캡처용 logger mock
# ---------------------------------------------------------------------------


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, *, trace_id, module_name, event_type, payload):
        self.events.append({
            "trace_id": trace_id,
            "module_name": module_name,
            "event_type": event_type,
            "payload": payload,
        })

    def event_types(self):
        return [e["event_type"] for e in self.events]


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _detector(store=None, logger=None):
    return ContinuationDetector(
        cue_classifier=CueClassifier(),
        session_goal_store=store or InMemorySessionGoalStore(),
        logger=logger or CapturingLogger(),
    )


async def _add_active_goal(store, session_id: str, title="active task", category="coding"):
    ctx = await store.get_or_create_session(session_id)
    goal = make_goal(title=title, source="user_explicit", category=category)
    ctx.add_goal(goal)
    ctx.set_active(goal.goal_id)
    return goal


# ---------------------------------------------------------------------------
# session_id 없음 → no_session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_session_id_fails_open():
    det = _detector()
    decision = await det.detect("계속해", session_id=None, trace_id="t-1")
    assert decision.should_bypass is False
    assert decision.reason == "no_session_id"
    assert decision.active_goal_snapshot is None


@pytest.mark.asyncio
async def test_no_session_id_empty_string():
    det = _detector()
    decision = await det.detect("계속해", session_id="", trace_id="t-1")
    assert decision.should_bypass is False
    assert decision.reason == "no_session_id"


# ---------------------------------------------------------------------------
# cue 없음 → no_continuation_cue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_continuation_cue():
    det = _detector()
    decision = await det.detect("안녕", session_id="s-1", trace_id="t-1")
    assert decision.should_bypass is False
    assert decision.reason == "no_continuation_cue"


@pytest.mark.asyncio
async def test_completion_cue_not_continuation():
    """completion cue는 continuation이 아니므로 bypass 안 함."""
    det = _detector()
    decision = await det.detect("완료했어", session_id="s-1", trace_id="t-1")
    assert decision.should_bypass is False
    assert decision.reason == "no_continuation_cue"


# ---------------------------------------------------------------------------
# active_goal 없음 → no_active_goal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_no_active_goal():
    store = InMemorySessionGoalStore()
    det = _detector(store=store)
    decision = await det.detect("계속해", session_id="s-1", trace_id="t-1")
    assert decision.should_bypass is False
    assert decision.reason == "no_active_goal"


# ---------------------------------------------------------------------------
# continuation + active_goal → bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_with_active_goal_bypasses():
    store = InMemorySessionGoalStore()
    await _add_active_goal(store, "s-1", title="my coding task", category="coding")
    det = _detector(store=store)
    decision = await det.detect("계속해", session_id="s-1", trace_id="t-1")
    assert decision.should_bypass is True
    assert decision.reason == "bypass"


@pytest.mark.asyncio
async def test_bypass_snapshot_carries_goal_info():
    store = InMemorySessionGoalStore()
    goal = await _add_active_goal(store, "s-1", title="data analysis", category="data_analysis")
    det = _detector(store=store)
    decision = await det.detect("계속", session_id="s-1", trace_id="t-1")
    snap = decision.active_goal_snapshot
    assert snap is not None
    assert snap.detected is True
    assert snap.active_goal_id == goal.goal_id
    assert snap.active_goal_title == "data analysis"
    assert snap.active_goal_category == "data_analysis"
    assert snap.cue_keyword is not None


# ---------------------------------------------------------------------------
# snapshot이 순수 Pydantic 모델 (lock/queue 객체 없음)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_is_pure_pydantic():
    store = InMemorySessionGoalStore()
    await _add_active_goal(store, "s-1")
    det = _detector(store=store)
    decision = await det.detect("계속해", session_id="s-1", trace_id="t-1")
    assert isinstance(decision.active_goal_snapshot, ContinuationContext)
    # JSON 직렬화 가능 — 어떤 runtime 객체도 포함 안 됨
    dumped = decision.active_goal_snapshot.model_dump()
    assert "goal_stack" not in dumped
    assert "store" not in dumped


# ---------------------------------------------------------------------------
# store mutation 0건
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detector_does_not_mutate_store():
    """Detector는 read-only — touch/update/set_active 호출 안 함."""
    store = InMemorySessionGoalStore()
    goal = await _add_active_goal(store, "s-1")
    ctx_before = await store.get_or_create_session("s-1")
    last_used_before = ctx_before.goal_stack.get(goal.goal_id).last_used_at
    updated_at_before = ctx_before.updated_at

    det = _detector(store=store)
    await det.detect("계속해", session_id="s-1", trace_id="t-1")

    ctx_after = await store.get_or_create_session("s-1")
    last_used_after = ctx_after.goal_stack.get(goal.goal_id).last_used_at
    updated_at_after = ctx_after.updated_at
    assert last_used_before == last_used_after, "Detector touched goal"
    assert updated_at_before == updated_at_after, "Detector mutated context"


# ---------------------------------------------------------------------------
# store 예외 → detector_error, bypass=False
# ---------------------------------------------------------------------------


class _BrokenStore:
    async def get_or_create_session(self, session_id):
        raise RuntimeError("store broken")


@pytest.mark.asyncio
async def test_store_exception_fails_open():
    det = ContinuationDetector(
        cue_classifier=CueClassifier(),
        session_goal_store=_BrokenStore(),  # type: ignore[arg-type]
        logger=CapturingLogger(),
    )
    decision = await det.detect("계속해", session_id="s-1", trace_id="t-1")
    assert decision.should_bypass is False
    assert decision.reason == "detector_error"


# ---------------------------------------------------------------------------
# CancelledError → re-raise
# ---------------------------------------------------------------------------


class _CancellingStore:
    async def get_or_create_session(self, session_id):
        raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_cancelled_error_propagates():
    det = ContinuationDetector(
        cue_classifier=CueClassifier(),
        session_goal_store=_CancellingStore(),  # type: ignore[arg-type]
        logger=CapturingLogger(),
    )
    with pytest.raises(asyncio.CancelledError):
        await det.detect("계속해", session_id="s-1", trace_id="t-1")


# ---------------------------------------------------------------------------
# Logger 실패 → fail-open
# ---------------------------------------------------------------------------


class _BrokenLogger:
    async def log_event(self, **kwargs):
        raise RuntimeError("logger broken")


@pytest.mark.asyncio
async def test_logger_failure_does_not_block_detector():
    store = InMemorySessionGoalStore()
    await _add_active_goal(store, "s-1")
    det = ContinuationDetector(
        cue_classifier=CueClassifier(),
        session_goal_store=store,
        logger=_BrokenLogger(),
    )
    decision = await det.detect("계속해", session_id="s-1", trace_id="t-1")
    assert decision.should_bypass is True
