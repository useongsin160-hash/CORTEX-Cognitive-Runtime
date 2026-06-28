"""Phase 5 STEP 5 — routes.py continuation bypass 통합 테스트.

continuation cue + active_goal 결합 시 Thalamus/Cache/Tier-1.5를 모두 우회하고
AsyncSwarm으로 직접 분기되는지 확인한다.
"""
from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ingress.exact_cache import ExactCache
from app.ingress.semantic_cache import SemanticCache
from app.main import app
from app.memory.goal import make_goal


# ---------------------------------------------------------------------------
# Spy wrappers — 호출 횟수 추적
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


class _SpySemanticCache:
    def __init__(self) -> None:
        self.get_calls = 0
        self.put_calls = 0
        self.collection = None

    async def get(self, prompt, threshold=0.0, **_ns):
        self.get_calls += 1
        return None

    async def put(self, prompt, response):
        self.put_calls += 1


class _SpyTier15:
    def __init__(self) -> None:
        self.should_activate_calls = 0
        self.execute_calls = 0

    async def should_activate(self, task_context, similarity):
        self.should_activate_calls += 1
        return False

    async def execute(self, prompt, cached):
        self.execute_calls += 1
        return "tier15 result"


class _SpySanitizer:
    def __init__(self, real) -> None:
        self._real = real
        self.calls = 0

    async def sanitize(self, prompt, trace_id):
        self.calls += 1
        return await self._real.sanitize(prompt, trace_id=trace_id)


class _SpyGlycine:
    def __init__(self, real) -> None:
        self._real = real
        self.calls = 0

    async def check_pre_flight(self, prompt, session_key):
        self.calls += 1
        return await self._real.check_pre_flight(prompt=prompt, session_key=session_key)


class _SpySwarm:
    def __init__(self, real) -> None:
        self._real = real
        self.calls = 0

    async def execute(self, task_context, query_features=None):
        self.calls += 1
        return await self._real.execute(
            task_context=task_context, query_features=query_features,
        )


# ---------------------------------------------------------------------------
# Fixture: 스파이로 감싼 TestClient + active_goal 사전 등록
# ---------------------------------------------------------------------------


@pytest.fixture
def spied_client(app_client) -> Iterator[tuple]:
    # app_client (conftest): 세션 app 1회 lifespan + per-test tmp ExactCache + semantic/exact
    # 원복. 모든 파이프라인 컴포넌트를 스파이로 덮고 finally 에서 원복한다(실 chroma 미생성 —
    # 기존엔 실 ExactCache/SemanticCache 를 만들었다가 곧장 스파이로 덮어쓰는 사장 코드였다).
    c = app_client
    # Save real components for restoration
    saved = {
        "sanitizer": c.app.state.sanitizer,
        "glycine": c.app.state.glycine,
        "thalamus": c.app.state.thalamus,
        "exact_cache": c.app.state.exact_cache,
        "semantic_cache": c.app.state.semantic_cache,
        "tier15": c.app.state.tier15,
        "async_swarm": c.app.state.async_swarm,
    }
    # Phase 6 STEP 3.2: routes.py calls rpe_pipeline.execute() which
    # delegates to rpe_pipeline._inner_swarm. Save and restore it.
    saved_rpe_inner = c.app.state.rpe_pipeline._inner_swarm
    try:
        spy_sanitizer = _SpySanitizer(saved["sanitizer"])
        spy_glycine = _SpyGlycine(saved["glycine"])
        spy_thalamus = _SpyThalamus()
        spy_exact = _SpyExactCache()
        spy_semantic = _SpySemanticCache()
        spy_tier15 = _SpyTier15()
        spy_swarm = _SpySwarm(saved["async_swarm"])

        c.app.state.sanitizer = spy_sanitizer
        c.app.state.glycine = spy_glycine
        c.app.state.thalamus = spy_thalamus
        c.app.state.exact_cache = spy_exact
        c.app.state.semantic_cache = spy_semantic
        c.app.state.tier15 = spy_tier15
        c.app.state.async_swarm = spy_swarm
        # Phase 6 STEP 3.2: inject spy into rpe_pipeline inner swarm
        c.app.state.rpe_pipeline._inner_swarm = spy_swarm

        yield c, {
            "sanitizer": spy_sanitizer,
            "glycine": spy_glycine,
            "thalamus": spy_thalamus,
            "exact_cache": spy_exact,
            "semantic_cache": spy_semantic,
            "tier15": spy_tier15,
            "swarm": spy_swarm,
        }
    finally:
        for k, v in saved.items():
            setattr(c.app.state, k, v)
        c.app.state.rpe_pipeline._inner_swarm = saved_rpe_inner


def _run_async(coro):
    """Run a coroutine in a fresh event loop — pytest 8 compatible."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _seed_active_goal(client, session_id: str):
    store = client.app.state.session_goal_store
    ctx = await store.get_or_create_session(session_id)
    goal = make_goal(title="my active task", source="user_explicit", category="coding")
    ctx.add_goal(goal)
    ctx.set_active(goal.goal_id)
    return goal


# ---------------------------------------------------------------------------
# continuation + active_goal → bypass
# ---------------------------------------------------------------------------


def test_continuation_bypasses_thalamus_and_caches(spied_client):
    client, spies = spied_client
    _run_async(_seed_active_goal(client, "sess-bypass-1"))

    response = client.post(
        "/query",
        json={"prompt": "계속해", "session_id": "sess-bypass-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["response_source"] == "swarm"
    assert data["path_taken"].startswith("routed_")
    assert data["swarm_trace"] is not None

    # Sanitizer/Glycine은 호출됨
    assert spies["sanitizer"].calls == 1
    assert spies["glycine"].calls == 1

    # Thalamus/Cache/Tier-1.5는 호출 안 됨
    assert spies["thalamus"].calls == 0
    assert spies["exact_cache"].get_calls == 0
    assert spies["semantic_cache"].get_calls == 0
    assert spies["tier15"].should_activate_calls == 0
    assert spies["tier15"].execute_calls == 0

    # AsyncSwarm은 1번 호출됨
    assert spies["swarm"].calls == 1


def test_continuation_no_session_id_uses_normal_path(spied_client):
    """session_id 없으면 bypass 안 함 — Thalamus가 호출되어야 함."""
    client, spies = spied_client
    response = client.post("/query", json={"prompt": "계속해"})
    assert response.status_code == 200
    assert spies["thalamus"].calls >= 1


def test_no_active_goal_uses_normal_path(spied_client):
    """active_goal 없으면 bypass 안 함."""
    client, spies = spied_client
    response = client.post(
        "/query",
        json={"prompt": "계속해", "session_id": "sess-no-goal"},
    )
    assert response.status_code == 200
    assert spies["thalamus"].calls >= 1


def test_no_continuation_cue_uses_normal_path(spied_client):
    """continuation cue 없으면 bypass 안 함."""
    client, spies = spied_client
    _run_async(_seed_active_goal(client, "sess-no-cue"))
    response = client.post(
        "/query",
        json={"prompt": "안녕 오늘 날씨 어때", "session_id": "sess-no-cue"},
    )
    assert response.status_code == 200
    assert spies["thalamus"].calls >= 1


def test_completion_cue_does_not_bypass(spied_client):
    """completion cue는 continuation이 아니므로 normal path."""
    client, spies = spied_client
    _run_async(_seed_active_goal(client, "sess-completion"))
    response = client.post(
        "/query",
        json={"prompt": "완료했어", "session_id": "sess-completion"},
    )
    assert response.status_code == 200
    assert spies["thalamus"].calls >= 1


def test_continuation_response_source_is_swarm(spied_client):
    """continuation bypass 응답의 response_source는 'swarm'."""
    client, _ = spied_client
    _run_async(_seed_active_goal(client, "sess-source"))
    response = client.post(
        "/query",
        json={"prompt": "이어서", "session_id": "sess-source"},
    )
    data = response.json()
    assert data["response_source"] == "swarm"
    # SwarmTrace schema 변경 없음 — 기본 필드 확인
    trace = data["swarm_trace"]
    assert "status" in trace
    assert "plan_intent" in trace
    assert "context_status" in trace
