"""B11 S3a — RPE biological routing override (label only).

Unit: learned weight shifts the path band ±1 (clamped at edges); unlearned cell
(None) leaves the B12 path untouched; mid band keeps; skip_layers preserved.
E2E: a seeded learned cell promotes the routed path; difficulty + tier unchanged
(override is path-only — the B12 난이도→tier mapping is not touched).
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import SpinalLogger
from app.rpe.difficulty_store import InMemorySynapseDifficultyWeightStore
from app.routing.rpe_route_override import DifficultyRouteOverride
from app.routing.skip_router import RouteDecision


def _ov(store: InMemorySynapseDifficultyWeightStore) -> DifficultyRouteOverride:
    return DifficultyRouteOverride(store=store, logger=SpinalLogger())


def _tc(category: str = "coding", difficulty: Difficulty = Difficulty.MEDIUM) -> TaskContext:
    return TaskContext(trace_id="t", category=category, difficulty=difficulty)


def _dec(path: str, *, skip_layers: list[str] | None = None) -> RouteDecision:
    return RouteDecision(path=path, skip_layers=skip_layers or [], reason="b12")


# ── unit: no override on unlearned cell ────────────────────────────────────
@pytest.mark.asyncio
async def test_unlearned_cell_no_override():
    store = InMemorySynapseDifficultyWeightStore()  # empty → None
    out = await _ov(store).apply(_dec("standard"), _tc(difficulty=Difficulty.MEDIUM), "s")
    assert out.path == "standard"
    assert out.reason == "b12"


# ── unit: promote / demote ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_high_weight_promotes_one_band():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 2): 0.8})
    out = await _ov(store).apply(_dec("standard"), _tc("coding", Difficulty.MEDIUM), "s")
    assert out.path == "full_pipeline"
    assert "rpe_override=promote" in out.reason


@pytest.mark.asyncio
async def test_low_weight_demotes_one_band():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 2): 0.2})
    out = await _ov(store).apply(_dec("standard"), _tc("coding", Difficulty.MEDIUM), "s")
    assert out.path == "lightweight"
    assert "rpe_override=demote" in out.reason


@pytest.mark.asyncio
async def test_mid_band_weight_keeps_path():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 2): 0.5})
    out = await _ov(store).apply(_dec("standard"), _tc("coding", Difficulty.MEDIUM), "s")
    assert out.path == "standard"
    assert out.reason == "b12"


# ── unit: edge clamps (±1 cannot exceed the band range) ────────────────────
@pytest.mark.asyncio
async def test_promote_clamped_at_full_pipeline():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 4): 0.9})
    out = await _ov(store).apply(
        _dec("full_pipeline"), _tc("coding", Difficulty.VERY_HARD), "s"
    )
    assert out.path == "full_pipeline"  # already top — no shift


@pytest.mark.asyncio
async def test_demote_clamped_at_lightweight():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 1): 0.1})
    out = await _ov(store).apply(_dec("lightweight"), _tc("coding", Difficulty.EASY), "s")
    assert out.path == "lightweight"  # already bottom — no shift


# ── unit: skip_layers preserved (execution meaning deferred to S3b) ────────
@pytest.mark.asyncio
async def test_skip_layers_preserved_on_override():
    store = InMemorySynapseDifficultyWeightStore({("s", "coding", 2): 0.8})
    out = await _ov(store).apply(
        _dec("standard", skip_layers=["full_planner"]),
        _tc("coding", Difficulty.MEDIUM),
        "s",
    )
    assert out.path == "full_pipeline"
    assert out.skip_layers == ["full_planner"]


# ── E2E: seeded cell promotes the routed path; tier/difficulty unchanged ───
class _FakeSemanticCache:
    def __init__(self) -> None:
        self.next_result: tuple[str, float] | None = None

    async def get(self, prompt: str, threshold: float = 0.90, **_ns):
        if self.next_result is None:
            return None
        response, similarity = self.next_result
        return (response, similarity) if similarity >= threshold else None

    async def put(self, prompt: str, response: str) -> None:  # pragma: no cover
        pass


@pytest.fixture
def client(app_client) -> Iterator[TestClient]:
    app_client.app.state.semantic_cache = _FakeSemanticCache()
    yield app_client


def test_route_override_promotes_routed_path_e2e(client):
    sid = "s3a-e2e-promote"
    prompt = "how do I parse a CSV file in Python"

    # Baseline — no learned cell → B12 difficulty path.
    b0 = client.post("/query", json={"prompt": prompt, "session_id": sid}).json()
    assert b0["path_taken"].startswith("routed_")
    base_path = b0["path_taken"]
    category, difficulty, base_tier = b0["category"], b0["difficulty"], b0["selected_tier"]

    # Seed a high learned weight for the exact (category, difficulty) cell.
    client.app.state.rpe_difficulty_store.set(sid, category, difficulty, 0.9)

    b1 = client.post("/query", json={"prompt": prompt, "session_id": sid}).json()

    bands = ["routed_lightweight", "routed_standard", "routed_full_pipeline"]
    if base_path != "routed_full_pipeline":
        assert bands.index(b1["path_taken"]) == bands.index(base_path) + 1
    else:
        assert b1["path_taken"] == base_path  # already top — clamped
    # Override is path-only: tier (난이도→tier 1:1) and difficulty are untouched.
    assert b1["difficulty"] == difficulty
    assert b1["selected_tier"] == base_tier
