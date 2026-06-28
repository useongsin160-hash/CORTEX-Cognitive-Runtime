"""Phase 3.5 STEP 1 — /query end-to-end with Synapse Observe + Snapshot.

synapse_snapshot is a TaskContext field (not surfaced in QueryResponse),
so these tests verify behaviour through the Spinal trace events:
  - synapse.observed       — emitted on the post-Evaluator path
  - synapse.snapshot_taken — emitted ONLY on a Tier-1.5 miss (routed)
"""
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
    # 여기선 semantic_cache 만 결정론 fake 로 교체한다(독립 lifespan 재진입·실 chroma 없음).
    app_client.app.state.semantic_cache = _FakeSemanticCache()
    yield app_client


def _events(client: TestClient, trace_id: str) -> list[str]:
    resp = client.get(f"/trace/{trace_id}")
    assert resp.status_code == 200
    return [e["event_type"] for e in resp.json()["events"]]


def _snapshot_event(client: TestClient, trace_id: str) -> dict | None:
    resp = client.get(f"/trace/{trace_id}")
    for e in resp.json()["events"]:
        if e["event_type"] == "synapse.snapshot_taken":
            return e
    return None


# ── early-exit paths: no Observe, no Snapshot ───────────────────────────────
def test_thalamus_path_skips_synapse(client):
    resp = client.post("/query", json={"prompt": "안녕"})
    trace_id = resp.json()["trace_id"]
    types = _events(client, trace_id)
    assert "synapse.observed" not in types
    assert "synapse.snapshot_taken" not in types


def test_exact_cache_path_skips_synapse(client):
    prompt = "verbatim prompt seeded into exact cache"
    asyncio.run(client.app.state.exact_cache.put(prompt, "cached answer"))
    resp = client.post("/query", json={"prompt": prompt})
    trace_id = resp.json()["trace_id"]
    types = _events(client, trace_id)
    assert "synapse.observed" not in types
    assert "synapse.snapshot_taken" not in types


def test_semantic_cache_path_skips_synapse(client):
    client.app.state.semantic_cache.next_result = ("cached semantic", 0.95)
    resp = client.post(
        "/query", json={"prompt": "please tell me about distributed databases"},
    )
    trace_id = resp.json()["trace_id"]
    types = _events(client, trace_id)
    assert "synapse.observed" not in types
    assert "synapse.snapshot_taken" not in types


# ── tier_1_5 path: Observe ran, Snapshot did NOT ────────────────────────────
def test_tier_1_5_path_observes_but_does_not_snapshot(client):
    client.app.state.semantic_cache.next_result = ("older similar answer", 0.80)
    resp = client.post(
        "/query", json={"prompt": "tell me about cats and parrots and lizards"},
    )
    body = resp.json()
    assert body["path_taken"] == "tier_1_5"
    types = _events(client, body["trace_id"])
    assert "synapse.observed" in types
    assert "synapse.snapshot_taken" not in types


# ── routed path: Observe + Snapshot both run ────────────────────────────────
def test_routed_path_observes_and_snapshots(client):
    resp = client.post(
        "/query",
        json={"prompt": "help me debug this python script with a null pointer"},
    )
    body = resp.json()
    assert body["path_taken"].startswith("routed_")
    types = _events(client, body["trace_id"])
    assert "synapse.observed" in types
    assert "synapse.snapshot_taken" in types
    # snapshot must carry all 7 category weights
    snap_event = _snapshot_event(client, body["trace_id"])
    assert snap_event is not None
    assert snap_event["payload"]["category_count"] == 7


# ── session isolation ───────────────────────────────────────────────────────
def test_same_session_shares_synapse_store(client):
    store = client.app.state.synapse_store
    client.post(
        "/query",
        json={"prompt": "help me debug this python function", "session_id": "shared"},
    )
    client.post(
        "/query",
        json={"prompt": "design a payments architecture", "session_id": "shared"},
    )
    # One SynapseState object for the shared session, observed twice.
    state = asyncio.run(store.get_state("shared"))
    assert state.last_observed_category is not None


def test_distinct_sessions_have_independent_state(client):
    store = client.app.state.synapse_store
    client.post(
        "/query",
        json={"prompt": "help me debug this python function", "session_id": "sess-A"},
    )
    client.post(
        "/query",
        json={"prompt": "design a payments architecture", "session_id": "sess-B"},
    )
    state_a = asyncio.run(store.get_state("sess-A"))
    state_b = asyncio.run(store.get_state("sess-B"))
    assert state_a is not state_b
