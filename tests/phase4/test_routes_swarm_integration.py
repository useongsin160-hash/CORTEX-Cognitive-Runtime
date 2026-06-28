"""Phase 4 STEP 3.3b — routes에서 AsyncSwarm 호출 통합.

각 path별로 swarm.execute 호출 여부를 spy로 검증한다.
  - early-exit 4종 (thalamus / exact_cache / semantic_cache / tier_1_5)
    → 호출 0건, swarm_trace=None
  - routed → 호출 1건, swarm_trace 채워짐
"""
from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult
from app.ingress.exact_cache import ExactCache
from app.main import app


class _SpyAsyncSwarm:
    """AsyncSwarm 대체 — execute 호출 횟수를 기록하고 정상 SwarmResult 반환."""

    def __init__(self) -> None:
        self.execute_call_count = 0
        self.last_task_context = None
        self.last_query_features = None

    async def execute(self, task_context, query_features=None):
        self.execute_call_count += 1
        self.last_task_context = task_context
        self.last_query_features = query_features
        return SwarmResult(
            context_result=ContextAgentResult(),
            final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
            generator_result=GeneratorResult(
                text="spy-output", tier_used="STANDARD", model_name="mock",
                prompt_tokens=1, completion_tokens=1, finish_reason="stop",
                latency_ms=1.0, ne_applied=False, plan_intent="answer",
            ),
            context_status="ok",
            planner_status="ok",
            generator_status="ok",
            total_elapsed_ms=42.0,
        )


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
def client(app_client) -> Iterator[tuple[TestClient, _SpyAsyncSwarm]]:
    # app_client (conftest): 세션 app 1회 lifespan + per-test tmp ExactCache + semantic/exact
    # 원복. async_swarm·rpe_pipeline._inner_swarm 은 이 모듈이 직접 save/restore 한다.
    c = app_client
    saved_swarm = c.app.state.async_swarm
    # Phase 6 STEP 3.2: routes.py now calls rpe_pipeline.execute()
    # which delegates to rpe_pipeline._inner_swarm. We must inject the
    # spy into the pipeline's inner swarm too.
    saved_inner = c.app.state.rpe_pipeline._inner_swarm
    try:
        c.app.state.semantic_cache = _FakeSemanticCache()
        spy = _SpyAsyncSwarm()
        c.app.state.async_swarm = spy
        c.app.state.rpe_pipeline._inner_swarm = spy
        yield c, spy
    finally:
        c.app.state.async_swarm = saved_swarm
        c.app.state.rpe_pipeline._inner_swarm = saved_inner


# ── early-exit paths: swarm.execute 호출 0건 ────────────────────────────────
def test_thalamus_path_does_not_call_swarm(client):
    c, spy = client
    resp = c.post("/query", json={"prompt": "안녕"})
    body = resp.json()
    assert spy.execute_call_count == 0
    assert body["response_source"] == "thalamus"
    assert body["swarm_trace"] is None


def test_exact_cache_hit_does_not_call_swarm(client):
    c, spy = client
    prompt = "memorized for swarm integration"
    asyncio.run(c.app.state.exact_cache.put(prompt, "cached"))
    resp = c.post("/query", json={"prompt": prompt})
    body = resp.json()
    assert spy.execute_call_count == 0
    assert body["response_source"] == "exact_cache"
    assert body["swarm_trace"] is None


def test_semantic_cache_hit_does_not_call_swarm(client):
    c, spy = client
    c.app.state.semantic_cache.next_result = ("cached", 0.95)
    resp = c.post("/query", json={"prompt": "tell me about distributed dbs"})
    body = resp.json()
    assert spy.execute_call_count == 0
    assert body["response_source"] == "semantic_cache"
    assert body["swarm_trace"] is None


def test_tier_1_5_path_does_not_call_swarm(client):
    c, spy = client
    c.app.state.semantic_cache.next_result = ("older similar answer", 0.80)
    resp = c.post(
        "/query",
        json={"prompt": "tell me about cats and parrots and lizards"},
    )
    body = resp.json()
    assert spy.execute_call_count == 0
    assert body["response_source"] == "tier_1_5"
    assert body["swarm_trace"] is None


# ── routed path: swarm.execute 호출 1건 + swarm_trace 채워짐 ──────────────
def test_routed_path_calls_swarm_once(client):
    c, spy = client
    resp = c.post(
        "/query",
        json={"prompt": "help me debug this python script with a null pointer"},
    )
    body = resp.json()
    assert spy.execute_call_count == 1
    assert body["response_source"] == "swarm"
    assert body["swarm_trace"] is not None
    trace = body["swarm_trace"]
    assert trace["executed"] is True
    assert trace["status"] in {"ok", "degraded", "error", "timeout"}
    assert trace["elapsed_ms"] is not None
    assert trace["elapsed_ms"] > 0
    assert trace["plan_intent"] is not None


def test_routed_path_forwards_query_features_to_swarm(client):
    c, spy = client
    c.post(
        "/query",
        json={"prompt": "design a payments architecture across regions"},
    )
    assert spy.last_query_features is not None
    # SemanticEvaluator의 embedding이 QueryFeatures로 전달돼야 한다 (ADR-002 부분 활용).
    assert spy.last_query_features.raw_query == \
        "design a payments architecture across regions"
    assert spy.last_query_features.embedding is not None
    assert spy.last_query_features.embedding_source == "evaluator"
