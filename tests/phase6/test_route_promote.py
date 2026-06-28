"""B11 S3b-promote — Epinephrine limit-break expands Context category scope.

Unit: CategorySelector threshold override (0.2 includes weak 0.2~0.4 categories,
bounded — <0.2 still excluded); ContextAgent passes 0.2 only when epinephrine_active.
Structural read-only: ContextAgent never references the 35-cell difficulty store.
E2E: a full_pipeline request triggers epinephrine_active=True and writes 0 cells
to the difficulty store (limit-break is read-only — ratchet-conflict guard).
"""
from __future__ import annotations

import inspect
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.execution.context_agent as context_agent_module
from app.api.schemas.context import Difficulty, TaskContext
from app.execution.category_selector import CategorySelector
from app.execution.context_agent import LIMIT_BREAK_THRESHOLD, ContextAgent


# ── CategorySelector: threshold override + bounded expansion ────────────────
_SNAP = {"coding": 0.5, "writing": 0.3, "math_logic": 0.25, "general": 0.1}


def test_default_threshold_excludes_weak_categories():
    selected, _ = CategorySelector().select(_SNAP, "coding")  # threshold 0.4
    assert selected == ["coding"]


def test_limit_break_threshold_includes_weak_but_bounded():
    selected, _ = CategorySelector().select(_SNAP, "coding", threshold=0.2)
    # 0.2~0.4 categories now pass...
    assert set(selected) == {"coding", "writing", "math_logic"}
    # ...but the 0.1 category is still excluded — bounded (거름망 넓힘, not 없앰).
    assert "general" not in selected


def test_threshold_none_uses_instance_default():
    selected, _ = CategorySelector().select(_SNAP, "coding", threshold=None)
    assert selected == ["coding"]


# ── ContextAgent: limit-break wiring ───────────────────────────────────────
def _agent_with_spy_selector() -> tuple[ContextAgent, MagicMock]:
    selector = MagicMock()
    selector.select = MagicMock(return_value=(["coding"], False))
    searcher = MagicMock()
    searcher.search = AsyncMock(return_value=[])
    gaba = MagicMock()
    gaba.filter = MagicMock(return_value=([], False))
    return ContextAgent(selector=selector, searcher=searcher, gaba=gaba), selector


def _tc(epinephrine_active: bool) -> TaskContext:
    return TaskContext(
        trace_id="t",
        prompt="q",
        category="coding",
        difficulty=Difficulty.VERY_HARD,
        epinephrine_active=epinephrine_active,
        synapse_snapshot={"coding": 0.3},
    )


@pytest.mark.asyncio
async def test_epinephrine_active_passes_limit_break_threshold():
    agent, selector = _agent_with_spy_selector()
    await agent.retrieve(_tc(epinephrine_active=True))
    assert selector.select.call_args.kwargs["threshold"] == LIMIT_BREAK_THRESHOLD
    assert LIMIT_BREAK_THRESHOLD == 0.2


@pytest.mark.asyncio
async def test_epinephrine_inactive_uses_default_threshold():
    agent, selector = _agent_with_spy_selector()
    await agent.retrieve(_tc(epinephrine_active=False))
    assert selector.select.call_args.kwargs["threshold"] is None


# ── Structural read-only: ContextAgent never touches the 35-cell store ─────
def test_context_agent_does_not_reference_difficulty_store():
    src = inspect.getsource(context_agent_module)
    assert "difficulty_store" not in src
    assert "rpe_difficulty" not in src


# ── E2E: full_pipeline → epinephrine active, store write 0 ─────────────────
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


def test_limit_break_active_on_full_pipeline_and_read_only_e2e(client):
    sid = "s3b-promote-e2e"
    # "design ... architecture" → hard keyword → difficulty 4 → full_pipeline.
    prompt = "design a scalable architecture for a payment system with tradeoffs"

    before = dict(client.app.state.rpe_difficulty_store.snapshot())
    body = client.post("/query", json={"prompt": prompt, "session_id": sid}).json()

    assert body["path_taken"] == "routed_full_pipeline"
    assert body["epinephrine_active"] is True            # redefined: path-driven
    assert body["epinephrine_reason"] == "limit_break"

    # Read-only: the limit-break retrieval path wrote nothing to the 35-cell store
    # (mock-mode learner also writes nothing) — ratchet-conflict guard.
    after = dict(client.app.state.rpe_difficulty_store.snapshot())
    assert after == before
