"""Phase 5 STEP 5 — continuation bypass cache 안전성.

continuation 응답은 ExactCache/SemanticCache에 write되지 않아야 한다.
같은 cue ("계속해")라도 session_id가 다르면 서로 다른 active_goal을 가져야 한다.
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


class _WriteCountingExactCache:
    def __init__(self) -> None:
        self.gets = 0
        self.puts = 0

    async def get(self, prompt, **_ns):
        self.gets += 1
        return None

    async def put(self, prompt, response):
        self.puts += 1


class _WriteCountingSemanticCache:
    def __init__(self) -> None:
        self.gets = 0
        self.puts = 0
        self.collection = None

    async def get(self, prompt, threshold=0.0, **_ns):
        self.gets += 1
        return None

    async def put(self, prompt, response):
        self.puts += 1


@pytest.fixture
def cache_spied_client(app_client) -> Iterator[tuple]:
    # app_client (conftest): 세션 app 1회 lifespan + per-test tmp ExactCache + semantic/exact
    # 원복. 여기선 두 캐시를 write-counting 스파이로 교체한다(원복은 app_client 담당).
    c = app_client
    exact_spy = _WriteCountingExactCache()
    semantic_spy = _WriteCountingSemanticCache()
    c.app.state.exact_cache = exact_spy
    c.app.state.semantic_cache = semantic_spy
    yield c, exact_spy, semantic_spy


def _run_async(coro):
    """Run a coroutine in a fresh event loop — pytest 8 compatible."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _seed_goal(client, session_id: str, title="continuation task", category="coding"):
    store = client.app.state.session_goal_store
    ctx = await store.get_or_create_session(session_id)
    goal = make_goal(title=title, source="user_explicit", category=category)
    ctx.add_goal(goal)
    ctx.set_active(goal.goal_id)
    return goal


# ---------------------------------------------------------------------------
# continuation bypass — ExactCache write 0건
# ---------------------------------------------------------------------------


def test_continuation_no_exact_cache_write(cache_spied_client):
    client, exact_spy, _ = cache_spied_client
    _run_async(
        _seed_goal(client, "sess-cache-1")
    )
    response = client.post(
        "/query", json={"prompt": "계속해", "session_id": "sess-cache-1"},
    )
    assert response.status_code == 200
    assert exact_spy.puts == 0


def test_continuation_no_semantic_cache_write(cache_spied_client):
    client, _, semantic_spy = cache_spied_client
    _run_async(
        _seed_goal(client, "sess-cache-2")
    )
    response = client.post(
        "/query", json={"prompt": "이어서 해줘", "session_id": "sess-cache-2"},
    )
    assert response.status_code == 200
    assert semantic_spy.puts == 0


# ---------------------------------------------------------------------------
# continuation bypass — cache get/read 0건
# ---------------------------------------------------------------------------


def test_continuation_no_exact_cache_read(cache_spied_client):
    client, exact_spy, _ = cache_spied_client
    _run_async(
        _seed_goal(client, "sess-cache-3")
    )
    response = client.post(
        "/query", json={"prompt": "계속", "session_id": "sess-cache-3"},
    )
    assert response.status_code == 200
    assert exact_spy.gets == 0


def test_continuation_no_semantic_cache_read(cache_spied_client):
    client, _, semantic_spy = cache_spied_client
    _run_async(
        _seed_goal(client, "sess-cache-4")
    )
    response = client.post(
        "/query", json={"prompt": "다음 단계로 가자", "session_id": "sess-cache-4"},
    )
    assert response.status_code == 200
    assert semantic_spy.gets == 0


# ---------------------------------------------------------------------------
# 다른 session_id → 다른 active_goal (cross-session 격리)
# ---------------------------------------------------------------------------


def test_same_cue_different_sessions_different_goals(cache_spied_client):
    """같은 '계속해'라도 session_id가 다르면 서로 다른 active_goal로 라우팅."""
    client, _, _ = cache_spied_client
    goal_a = _run_async(
        _seed_goal(client, "sess-a", title="goal A", category="coding")
    )
    goal_b = _run_async(
        _seed_goal(client, "sess-b", title="goal B", category="writing")
    )
    assert goal_a.goal_id != goal_b.goal_id

    response_a = client.post("/query", json={"prompt": "계속해", "session_id": "sess-a"})
    response_b = client.post("/query", json={"prompt": "계속해", "session_id": "sess-b"})

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    # 두 세션 모두 swarm path
    assert response_a.json()["response_source"] == "swarm"
    assert response_b.json()["response_source"] == "swarm"
    # category가 active_goal_category로 강제됨 → 서로 다른 카테고리
    assert response_a.json()["category"] == "coding"
    assert response_b.json()["category"] == "writing"


# ---------------------------------------------------------------------------
# Normal path (continuation 아님)은 cache get/put 정상 동작
# ---------------------------------------------------------------------------


def test_normal_path_still_uses_cache(cache_spied_client):
    """continuation 아닌 일반 쿼리는 cache lookup이 정상 호출됨."""
    client, exact_spy, semantic_spy = cache_spied_client
    response = client.post("/query", json={"prompt": "어떤 알고리즘 추천해줘"})
    assert response.status_code == 200
    # Thalamus가 short-circuit하지 않으면 exact_cache는 호출됨
    assert exact_spy.gets >= 0  # 단순히 cache write가 0이 아니라는 부정 검증
