"""Phase 5 STEP 5 — routes.py Phase 4 호환성 회귀.

ContinuationDetector 미주입 또는 bypass=False일 때 기존 Phase 4 흐름이
100% 유지되는지 확인한다.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ingress.exact_cache import ExactCache
from app.ingress.semantic_cache import SemanticCache
from app.main import app


# ---------------------------------------------------------------------------
# Spies
# ---------------------------------------------------------------------------


class _SpyThalamus:
    def __init__(self) -> None:
        self.calls = 0

    async def should_short_circuit(self, prompt):
        self.calls += 1
        return False, None


class _SpyExactCache:
    def __init__(self) -> None:
        self.get_calls = 0
        self.put_calls = 0

    async def get(self, prompt, **_ns):
        self.get_calls += 1
        return None

    async def put(self, prompt, response):
        self.put_calls += 1


@pytest.fixture
def compat_client(app_client, make_ephemeral_cache) -> Iterator[tuple]:
    """state를 변경하므로 fixture 종료 시 복구. app_client (conftest)가 세션 app 1회
    lifespan + per-test tmp ExactCache + semantic/exact 원복을 담당하고, thalamus·
    continuation_detector 는 이 모듈이 직접 save/restore 한다. semantic 은 실 e5 ephemeral
    (독립 in-memory, PersistentClient 누수/락 없음)."""
    c = app_client
    saved_thalamus = c.app.state.thalamus
    saved_detector = c.app.state.continuation_detector
    c.app.state.semantic_cache = make_ephemeral_cache(real=True)
    try:
        yield c
    finally:
        c.app.state.thalamus = saved_thalamus
        c.app.state.continuation_detector = saved_detector


# ---------------------------------------------------------------------------
# Detector 미주입 → Phase 4 흐름 100% 유지
# ---------------------------------------------------------------------------


def test_no_detector_uses_phase4_flow(compat_client):
    spy_thalamus = _SpyThalamus()
    compat_client.app.state.thalamus = spy_thalamus
    # detector 미주입
    compat_client.app.state.continuation_detector = None

    response = compat_client.post(
        "/query",
        json={"prompt": "계속해", "session_id": "s-no-detector"},
    )
    assert response.status_code == 200
    # detector 없으니 Thalamus 정상 호출됨 (continuation cue여도 bypass 안 함)
    assert spy_thalamus.calls == 1


# ---------------------------------------------------------------------------
# bypass=False (no_continuation_cue) → 기존 Phase 4 흐름
# ---------------------------------------------------------------------------


def test_no_continuation_cue_phase4_flow(compat_client):
    spy_thalamus = _SpyThalamus()
    spy_exact = _SpyExactCache()
    compat_client.app.state.thalamus = spy_thalamus
    compat_client.app.state.exact_cache = spy_exact

    response = compat_client.post(
        "/query",
        json={"prompt": "Implement async retry logic"},
    )
    assert response.status_code == 200
    # 정상 path → Thalamus / ExactCache 호출됨
    assert spy_thalamus.calls == 1
    assert spy_exact.get_calls == 1


# ---------------------------------------------------------------------------
# Thalamus short-circuit 짧은 인사말 — early-exit 유지
# ---------------------------------------------------------------------------


def test_short_greeting_still_thalamus(compat_client):
    response = compat_client.post("/query", json={"prompt": "안녕"})
    assert response.status_code == 200
    data = response.json()
    assert data["path_taken"] == "thalamus"
    assert data["response_source"] == "thalamus"


# ---------------------------------------------------------------------------
# SwarmTrace 구조 유지 (continuation 관련 필드 없음)
# ---------------------------------------------------------------------------


def test_swarm_trace_no_continuation_fields(compat_client):
    response = compat_client.post(
        "/query",
        json={"prompt": "Implement async retry logic with exponential backoff in Python"},
    )
    data = response.json()
    if data.get("swarm_trace"):
        trace = data["swarm_trace"]
        # SwarmTrace에 continuation 관련 필드 추가 금지
        for key in trace.keys():
            assert "continuation" not in key.lower()


# ---------------------------------------------------------------------------
# early-exit 경로는 AsyncSwarm 호출 0건 (Phase 4 회귀)
# ---------------------------------------------------------------------------


class _SpySwarm:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, task_context, query_features=None):
        self.calls += 1
        from app.execution.swarm_models import SwarmResult
        # never reaches here in early-exit case
        raise RuntimeError("swarm called in early-exit path")


def test_thalamus_early_exit_no_swarm(compat_client):
    spy_swarm = _SpySwarm()
    compat_client.app.state.async_swarm = spy_swarm
    response = compat_client.post("/query", json={"prompt": "ping"})
    assert response.status_code == 200
    # thalamus 짧은 응답 → swarm 호출 안 됨
    assert spy_swarm.calls == 0


# ---------------------------------------------------------------------------
# response 구조 (continuation_context 필드 없음 — schema 변경 0건)
# ---------------------------------------------------------------------------


def test_response_does_not_leak_continuation_context(compat_client):
    """QueryResponse에 continuation_context 필드가 노출되지 않음."""
    response = compat_client.post(
        "/query",
        json={"prompt": "Hello"},
    )
    data = response.json()
    assert "continuation_context" not in data
