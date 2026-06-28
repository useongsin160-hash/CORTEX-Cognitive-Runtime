"""Phase 5 STEP 5 — continuation bypass 안전성 회귀.

핵심 불변식:
- Sanitizer가 차단하면 ContinuationDetector는 호출되지 않는다.
- Glycine이 차단하면 Detector/Thalamus/Swarm은 호출되지 않는다.
- Detector 실패 → normal path fail-open.
- session_id 변조 시 다른 세션 active_goal은 노출되지 않는다.
"""
from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ValidationError
from app.main import app
from app.memory.goal import make_goal


def _run_async(coro):
    """Run a coroutine in a fresh event loop — pytest 8 compatible."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class _BlockingSanitizer:
    def __init__(self) -> None:
        self.calls = 0

    async def sanitize(self, prompt, trace_id):
        self.calls += 1
        raise ValidationError("blocked by sanitizer")


class _BlockingGlycine:
    def __init__(self) -> None:
        self.calls = 0

    async def check_pre_flight(self, prompt, session_key):
        self.calls += 1
        from app.routing.neuromodulators import GlycineDecision
        return GlycineDecision(
            active=True,
            reason="rate_limit",
            action="hard_brake",
        )


class _SpyContinuationDetector:
    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, query, session_id, trace_id):
        self.calls += 1
        # 안전 경로: never reaches here
        from app.routing.continuation_detector import ContinuationDecision
        from app.routing.cue_classifier import CueDetection
        return ContinuationDecision(
            should_bypass=False,
            cue_detection=CueDetection("none", "ko", None, 0.0),
            active_goal_snapshot=None,
            reason="no_continuation_cue",
        )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def safety_client(app_client) -> Iterator[TestClient]:
    """app.state는 싱글턴이므로 변경 후 반드시 복구한다.
    app_client (conftest): 세션 app 1회 lifespan + semantic/exact 원복. sanitizer·glycine·
    continuation_detector 는 이 모듈이 직접 save/restore 한다."""
    c = app_client
    saved_sanitizer = c.app.state.sanitizer
    saved_glycine = c.app.state.glycine
    saved_detector = c.app.state.continuation_detector
    try:
        yield c
    finally:
        c.app.state.sanitizer = saved_sanitizer
        c.app.state.glycine = saved_glycine
        c.app.state.continuation_detector = saved_detector


# ---------------------------------------------------------------------------
# Sanitizer 차단 → detector 호출 안 됨
# ---------------------------------------------------------------------------


def test_sanitizer_blocks_before_detector(safety_client):
    spy_detector = _SpyContinuationDetector()
    safety_client.app.state.sanitizer = _BlockingSanitizer()
    safety_client.app.state.continuation_detector = spy_detector

    response = safety_client.post("/query", json={"prompt": "계속해", "session_id": "s1"})
    # Sanitizer가 차단 → 400 + detector 호출 0건
    assert response.status_code == 400
    assert spy_detector.calls == 0


# ---------------------------------------------------------------------------
# Glycine 차단 → detector 호출 안 됨
# ---------------------------------------------------------------------------


def test_glycine_blocks_before_detector(safety_client):
    spy_detector = _SpyContinuationDetector()
    safety_client.app.state.glycine = _BlockingGlycine()
    safety_client.app.state.continuation_detector = spy_detector

    response = safety_client.post("/query", json={"prompt": "계속해", "session_id": "s1"})
    assert response.status_code == 200
    # path_taken은 "glycine_blocked"
    data = response.json()
    assert data["path_taken"] == "glycine_blocked"
    # detector는 절대 호출되지 않음
    assert spy_detector.calls == 0


# ---------------------------------------------------------------------------
# Detector 실패 → normal path fail-open
# ---------------------------------------------------------------------------


class _ExplodingDetector:
    async def detect(self, query, session_id, trace_id):
        raise RuntimeError("detector exploded")


def test_detector_explosion_does_not_500(safety_client):
    """Detector store 예외는 내부에서 fail-open으로 normal path 진행."""
    from app.core.logging import get_spinal_logger
    from app.routing.continuation_detector import ContinuationDetector
    from app.routing.cue_classifier import CueClassifier

    class _BrokenStore:
        async def get_or_create_session(self, session_id):
            raise RuntimeError("store down")

    real_detector = ContinuationDetector(
        cue_classifier=CueClassifier(),
        session_goal_store=_BrokenStore(),  # type: ignore[arg-type]
        logger=get_spinal_logger(),
    )
    safety_client.app.state.continuation_detector = real_detector

    response = safety_client.post(
        "/query",
        json={"prompt": "계속해", "session_id": "sess-explode"},
    )
    # detector_error → fail-open → normal path
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# session_id 변조 → 다른 세션 active_goal 노출 0건
# ---------------------------------------------------------------------------


def test_session_id_isolation(safety_client):
    """sess-A의 active_goal은 sess-B 요청에 노출되면 안 됨."""
    store = safety_client.app.state.session_goal_store

    async def _seed_a():
        ctx = await store.get_or_create_session("sess-iso-A")
        goal = make_goal(
            title="secret task A",
            source="user_explicit",
            category="coding",
        )
        ctx.add_goal(goal)
        ctx.set_active(goal.goal_id)
        return goal

    _run_async(_seed_a())

    # sess-B로 "계속해" 요청 — A의 goal에 닿으면 안 됨
    response = safety_client.post(
        "/query",
        json={"prompt": "계속해", "session_id": "sess-iso-B"},
    )
    assert response.status_code == 200
    # sess-B는 active_goal 없으니 normal path (bypass 안 함)
    data = response.json()
    # sess-B에는 active_goal 없으므로 bypass 안 함 → 정상 routed_/thalamus/cache 경로
    # 응답에 sess-A의 'secret task A' 문자열이 노출되지 않아야 한다
    assert "secret task A" not in (data.get("answer") or "")
