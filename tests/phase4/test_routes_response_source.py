"""Phase 4 STEP 3.3a — /query path별 response_source 라벨링."""
from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.ingress.exact_cache import ExactCache
from app.main import app


class _FakeSemanticCache:
    def __init__(self) -> None:
        self.next_result: tuple[str, float] | None = None

    async def get(self, prompt: str, threshold: float = 0.90, **_ns):
        if self.next_result is None:
            return None
        response, similarity = self.next_result
        if similarity < threshold:
            return None
        return response, similarity

    async def put(self, prompt: str, response: str) -> None:  # pragma: no cover
        pass


@pytest.fixture
def client(app_client) -> Iterator[TestClient]:
    # app_client (conftest): 세션 app 1회 lifespan + per-test tmp ExactCache + app.state 원복.
    app_client.app.state.semantic_cache = _FakeSemanticCache()
    yield app_client


# ── thalamus ─────────────────────────────────────────────────────────────
def test_thalamus_path_labels_response_source(client):
    resp = client.post("/query", json={"prompt": "안녕"})
    body = resp.json()
    assert body["path_taken"] == "thalamus"
    assert body["response_source"] == "thalamus"
    assert body["swarm_trace"] is None
    # 기존 규약 회귀 — early-exit 경로의 selected_tier=None.
    assert body["selected_tier"] is None


# ── exact_cache ──────────────────────────────────────────────────────────
def test_exact_cache_path_labels_response_source(client):
    prompt = "memorize this prompt for routes label test"
    asyncio.run(client.app.state.exact_cache.put(prompt, "cached"))
    resp = client.post("/query", json={"prompt": prompt})
    body = resp.json()
    assert body["path_taken"] == "exact_cache"
    assert body["response_source"] == "exact_cache"
    assert body["swarm_trace"] is None
    assert body["selected_tier"] is None


# ── semantic_cache ───────────────────────────────────────────────────────
def test_semantic_cache_path_labels_response_source(client):
    client.app.state.semantic_cache.next_result = ("cached semantic answer", 0.95)
    resp = client.post(
        "/query",
        json={"prompt": "please tell me about distributed databases"},
    )
    body = resp.json()
    assert body["path_taken"] == "semantic_cache"
    assert body["response_source"] == "semantic_cache"
    assert body["swarm_trace"] is None
    assert body["selected_tier"] is None


# ── tier_1_5 ─────────────────────────────────────────────────────────────
def test_tier_1_5_path_labels_response_source(client):
    # sub-0.90 similarity → Tier-1.5 trigger for difficulty-1 prompts.
    client.app.state.semantic_cache.next_result = ("older similar answer", 0.80)
    resp = client.post(
        "/query",
        json={"prompt": "tell me about cats and parrots and lizards"},
    )
    body = resp.json()
    assert body["path_taken"] == "tier_1_5"
    assert body["response_source"] == "tier_1_5"
    assert body["swarm_trace"] is None


# ── routed (swarm 라벨) ──────────────────────────────────────────────────
def test_routed_path_labels_response_source_as_swarm(client):
    resp = client.post(
        "/query",
        json={"prompt": "help me debug this python script with a null pointer"},
    )
    body = resp.json()
    assert body["path_taken"].startswith("routed_")
    assert body["response_source"] == "swarm"
    # STEP 3.3b: swarm_trace는 routed 경로에서 채워진다.
    # 본 테스트는 라벨 회귀이므로 trace 구조는 deep-검증하지 않는다
    # (그건 test_routes_swarm_integration.py의 책임).
    assert body["swarm_trace"] is not None
    # 기존 selected_tier 노출 회귀 — string으로 채워져야 한다.
    assert isinstance(body["selected_tier"], str)
