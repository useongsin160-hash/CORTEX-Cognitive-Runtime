"""Phase 5 STEP 5 — ContinuationDetector / routes logging 검증.

이벤트:
- continuation.no_session_id
- continuation.no_active_goal
- continuation.bypass_early_exit
- continuation.detector_error
- continuation.cache_bypassed
- 모든 이벤트에 trace_id 포함
- active_goal_id 기록, 원본 Goal 객체 기록 0건
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import ContinuationContext
from app.memory.store import InMemorySessionGoalStore
from app.memory.goal import make_goal
from app.routing.continuation_detector import ContinuationDetector
from app.routing.cue_classifier import CueClassifier


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

    def find(self, event_type):
        return next((e for e in self.events if e["event_type"] == event_type), None)


async def _seed_goal(store, session_id: str, category="coding"):
    ctx = await store.get_or_create_session(session_id)
    goal = make_goal(title="logging task", source="user_explicit", category=category)
    ctx.add_goal(goal)
    ctx.set_active(goal.goal_id)
    return goal


def _detector(store, logger):
    return ContinuationDetector(
        cue_classifier=CueClassifier(),
        session_goal_store=store,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# continuation.bypass_early_exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_early_exit_logged():
    store = InMemorySessionGoalStore()
    logger = CapturingLogger()
    goal = await _seed_goal(store, "s-1")
    det = _detector(store, logger)
    await det.detect("계속해", session_id="s-1", trace_id="trace-bypass")

    assert "continuation.bypass_early_exit" in logger.event_types()
    event = logger.find("continuation.bypass_early_exit")
    assert event["trace_id"] == "trace-bypass"
    assert event["payload"]["active_goal_id"] == goal.goal_id


# ---------------------------------------------------------------------------
# continuation.no_session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_session_id_logged():
    logger = CapturingLogger()
    det = _detector(InMemorySessionGoalStore(), logger)
    await det.detect("계속해", session_id=None, trace_id="trace-no-session")
    assert "continuation.no_session_id" in logger.event_types()
    event = logger.find("continuation.no_session_id")
    assert event["trace_id"] == "trace-no-session"


# ---------------------------------------------------------------------------
# continuation.no_active_goal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_active_goal_logged():
    store = InMemorySessionGoalStore()
    logger = CapturingLogger()
    det = _detector(store, logger)
    await det.detect("계속해", session_id="s-empty", trace_id="trace-no-goal")
    assert "continuation.no_active_goal" in logger.event_types()
    event = logger.find("continuation.no_active_goal")
    assert event["trace_id"] == "trace-no-goal"


# ---------------------------------------------------------------------------
# continuation.detector_error (store 예외)
# ---------------------------------------------------------------------------


class _ExplodingStore:
    async def get_or_create_session(self, session_id):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_detector_error_logged():
    logger = CapturingLogger()
    det = _detector(_ExplodingStore(), logger)  # type: ignore[arg-type]
    decision = await det.detect("계속해", session_id="s-err", trace_id="trace-err")
    assert decision.reason == "detector_error"
    assert "continuation.detector_error" in logger.event_types()
    event = logger.find("continuation.detector_error")
    assert event["trace_id"] == "trace-err"
    assert event["payload"]["error_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 모든 이벤트에 trace_id 포함
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_continuation_events_have_trace_id():
    """3개 시나리오에서 trace_id가 항상 기록됨."""
    store = InMemorySessionGoalStore()
    logger = CapturingLogger()
    await _seed_goal(store, "s-multi")
    det = _detector(store, logger)

    await det.detect("계속해", session_id=None, trace_id="t-A")
    await det.detect("안녕", session_id="s-empty", trace_id="t-B")
    await det.detect("계속해", session_id="s-multi", trace_id="t-C")

    continuation_events = [e for e in logger.events if e["event_type"].startswith("continuation.")]
    for event in continuation_events:
        assert event["trace_id"] in {"t-A", "t-B", "t-C"}, event


# ---------------------------------------------------------------------------
# active_goal 원본 객체 노출 0건 (payload에 goal_id만)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_payload_does_not_contain_goal_object():
    store = InMemorySessionGoalStore()
    logger = CapturingLogger()
    goal = await _seed_goal(store, "s-payload")
    det = _detector(store, logger)
    await det.detect("계속해", session_id="s-payload", trace_id="trace-payload")
    event = logger.find("continuation.bypass_early_exit")
    # payload는 원본 Goal Pydantic 객체를 직접 dump하지 않아야 함
    payload = event["payload"]
    # active_goal_id만 추출되어야 한다
    assert "active_goal_id" in payload
    # source / status 같은 내부 필드는 payload에 들어가지 않음
    assert "source" not in payload
    assert "status" not in payload
    assert "embedding" not in payload


# ---------------------------------------------------------------------------
# module_name 검증
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuation_events_module_name():
    store = InMemorySessionGoalStore()
    logger = CapturingLogger()
    await _seed_goal(store, "s-mod")
    det = _detector(store, logger)
    await det.detect("계속해", session_id="s-mod", trace_id="trace-mod")
    continuation_events = [
        e for e in logger.events if e["event_type"].startswith("continuation.")
    ]
    assert all(e["module_name"] == "routing.continuation_detector" for e in continuation_events)
