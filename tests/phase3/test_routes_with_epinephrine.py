"""Phase 3 STEP 3.2 — /query end-to-end with Epinephrine.

API serialization rules (spec corrections 3 + 5):
  - QueryResponse.selected_tier is `str | None`.
  - Early-exit paths (thalamus / exact_cache / semantic_cache / tier_1_5)
    set selected_tier = None.
  - LC-routed paths set selected_tier = ModelTier.name (never the int).

Uses the real CentroidStore + e5 embedder. SemanticCache is faked to
keep the cache exit deterministic per test.
"""
from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.model_tier import ModelTier
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


# ── Early-exit paths: selected_tier MUST be None ----------------------------
def test_thalamus_early_exit_selected_tier_is_none(client):
    resp = client.post("/query", json={"prompt": "안녕"})
    body = resp.json()
    assert body["path_taken"] == "thalamus"
    assert body["selected_tier"] is None
    assert body["epinephrine_active"] is False
    assert body["epinephrine_reason"] is None


def test_exact_cache_early_exit_selected_tier_is_none(client):
    prompt = "verbatim prompt seeded into exact cache"
    asyncio.run(client.app.state.exact_cache.put(prompt, "cached answer"))
    resp = client.post("/query", json={"prompt": prompt})
    body = resp.json()
    assert body["path_taken"] == "exact_cache"
    assert body["selected_tier"] is None
    assert body["epinephrine_active"] is False


def test_semantic_cache_early_exit_selected_tier_is_none(client):
    client.app.state.semantic_cache.next_result = ("cached semantic", 0.95)
    resp = client.post(
        "/query", json={"prompt": "please tell me about distributed databases"},
    )
    body = resp.json()
    assert body["path_taken"] == "semantic_cache"
    assert body["selected_tier"] is None
    assert body["epinephrine_active"] is False


def test_tier_1_5_early_exit_selected_tier_is_none(client):
    client.app.state.semantic_cache.next_result = ("older similar answer", 0.80)
    resp = client.post(
        "/query", json={"prompt": "tell me about cats and parrots and lizards"},
    )
    body = resp.json()
    assert body["path_taken"] == "tier_1_5"
    assert body["selected_tier"] is None
    assert body["epinephrine_active"] is False


# ── LC-routed paths: tier difficulty-driven (B12); Epinephrine = limit-break -
# B12: selected_tier == ModelTier(difficulty). B11 S3b-promote: epinephrine_active
# is REDEFINED — active iff the final path is full_pipeline (난이도/카테고리 무관),
# driving the ContextAgent limit-break. reason = "limit_break" / None.
def _assert_epinephrine_matches_path(body: dict) -> None:
    full = body["path_taken"] == "routed_full_pipeline"
    assert body["epinephrine_active"] == full
    assert body["epinephrine_reason"] == ("limit_break" if full else None)


def test_routed_selected_tier_matches_difficulty_one_to_one(client):
    resp = client.post(
        "/query",
        json={"prompt": "help me debug this python script with a null pointer"},
    )
    body = resp.json()
    assert body["path_taken"].startswith("routed_")
    assert body["category"] == "coding"
    assert body["selected_tier"] == ModelTier(body["difficulty"]).name
    _assert_epinephrine_matches_path(body)


def test_korean_coding_routed_tier_matches_difficulty(client):
    resp = client.post(
        "/query",
        json={"prompt": "이 파이썬 함수에서 NullPointerException 디버깅 좀 해줘"},
    )
    body = resp.json()
    assert body["path_taken"].startswith("routed_")
    assert body["category"] == "coding"
    assert body["selected_tier"] == ModelTier(body["difficulty"]).name
    _assert_epinephrine_matches_path(body)


def test_general_routed_tier_from_difficulty(client):
    # general → tier difficulty-driven (not category). Epinephrine follows path.
    resp = client.post(
        "/query",
        json={"prompt": "recommend a good weekend movie for tonight"},
    )
    body = resp.json()
    assert body["path_taken"].startswith("routed_")
    assert body["category"] == "general"
    assert body["selected_tier"] == ModelTier(body["difficulty"]).name
    _assert_epinephrine_matches_path(body)


def test_game_design_routed_tier_from_difficulty(client):
    # "design ..." → hard keyword → difficulty VERY_HARD(4) → HEAVY + full_pipeline
    # → epinephrine_active=True (limit-break), even though game_design is low-compute.
    resp = client.post(
        "/query",
        json={"prompt": "design a boss phase transition mechanic for the rpg"},
    )
    body = resp.json()
    assert body["path_taken"].startswith("routed_")
    assert body["category"] == "game_design"
    assert body["selected_tier"] == ModelTier(body["difficulty"]).name
    _assert_epinephrine_matches_path(body)


def test_selected_tier_never_leaks_intenum_value(client):
    """API serialization rule: the JSON value for selected_tier must be a
    string, never the underlying IntEnum int (e.g. 5 for DEEP_THINKING)."""
    resp = client.post(
        "/query",
        json={"prompt": "design a scalable payments architecture across regions"},
    )
    body = resp.json()
    tier = body["selected_tier"]
    assert tier is None or isinstance(tier, str)
    if isinstance(tier, str):
        assert not tier.isdigit(), f"selected_tier leaked numeric form: {tier}"
