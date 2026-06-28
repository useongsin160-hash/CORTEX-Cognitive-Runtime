"""B11 S3b-demote — lightweight route skips Context Agent retrieval.

Unit (swarm, pfc=None): route_path=="lightweight" → ContextAgent.retrieve is NOT
called (ChromaDB 0), context_status="skipped", generator still answers (graceful
no-context). Non-lightweight / None → context runs (back-compat).
E2E (post-S4 ratchet): a difficulty-1 request is natively lightweight → context
skipped; an override-demote of an already-floored cell is BLOCKED by the S4 ratchet
(demotion now comes only from S5 decay).
"""
from __future__ import annotations

from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.context import Difficulty, TaskContext
from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import GeneratorResult
from app.execution.planner_agent import PlannerAgent
from app.execution.swarm import AsyncSwarm


def _swarm() -> tuple[AsyncSwarm, MagicMock, MagicMock]:
    ctx = MagicMock()
    ctx.retrieve = AsyncMock(
        return_value=ContextAgentResult(
            selected_categories=["coding"], retrieved=[], filtered_count=0
        )
    )
    gen = MagicMock()
    gen.generate = AsyncMock(
        return_value=GeneratorResult(
            text="[MOCK] answer",
            tier_used="STANDARD",
            model_name="mock-model",
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason="stop",
            latency_ms=1.0,
            ne_applied=False,
            plan_intent="answer",
        )
    )
    swarm = AsyncSwarm(
        context_agent=ctx, planner_agent=PlannerAgent(), generator_agent=gen
    )
    return swarm, ctx, gen


def _tc(difficulty: Difficulty, route_path: str | None) -> TaskContext:
    return TaskContext(
        trace_id="t",
        prompt="hello",
        category="coding",
        difficulty=difficulty,
        route_path=route_path,
    )


@pytest.mark.asyncio
async def test_lightweight_skips_context_retrieval():
    swarm, ctx, gen = _swarm()
    result = await swarm.execute(_tc(Difficulty.EASY, "lightweight"))
    ctx.retrieve.assert_not_awaited()  # ChromaDB 0
    assert result.context_status == "skipped"
    assert result.context_result is None
    assert result.generator_result.text  # graceful answer without context
    gen.generate.assert_awaited()


@pytest.mark.asyncio
async def test_standard_runs_context_retrieval():
    swarm, ctx, gen = _swarm()
    result = await swarm.execute(_tc(Difficulty.MEDIUM, "standard"))
    ctx.retrieve.assert_awaited()
    assert result.context_status != "skipped"


@pytest.mark.asyncio
async def test_route_path_none_runs_context_backcompat():
    # Default None (e.g. legacy callers / direct swarm tests) → no skip.
    swarm, ctx, gen = _swarm()
    result = await swarm.execute(_tc(Difficulty.MEDIUM, None))
    ctx.retrieve.assert_awaited()
    assert result.context_status != "skipped"


# ── E2E: RPE demote → lightweight → context skipped ────────────────────────
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


def test_native_lightweight_skips_context_e2e(client):
    # A difficulty-1 routed request is natively lightweight (floor=lightweight, no
    # ratchet clamp) → Context Agent retrieval skipped. The skip mechanism survives S4.
    sid = "s4-native-light"
    prompt = "recommend a good weekend movie for tonight"  # difficulty 1 → lightweight

    body = client.post("/query", json={"prompt": prompt, "session_id": sid}).json()
    assert body["path_taken"] == "routed_lightweight"
    assert body["swarm_trace"]["context_status"] == "skipped"
    assert body["answer"]


def test_ratchet_blocks_override_demote_e2e(client):
    # B11 S4: the baseline request sets floor=standard; a later seed-low cell can
    # NO LONGER demote (ratchet). Demotion now comes only from S5 decay.
    sid = "s4-demote-blocked"
    prompt = "how do I parse a CSV file in Python"  # difficulty 2 → standard (B12)

    b0 = client.post("/query", json={"prompt": prompt, "session_id": sid}).json()
    assert b0["path_taken"] == "routed_standard"
    category, difficulty = b0["category"], b0["difficulty"]

    # Seed a LOW learned cell → S3a override WANTS lightweight, but floor=standard.
    client.app.state.rpe_difficulty_store.set(sid, category, difficulty, 0.2)

    b1 = client.post("/query", json={"prompt": prompt, "session_id": sid}).json()
    assert b1["path_taken"] == "routed_standard"               # demote blocked
    assert b1["swarm_trace"]["context_status"] != "skipped"    # context still ran
