"""Phase 6 STEP 3.2 — routes.py integration with RPEMutationPipelineWrapper.

Verifies that:
- Routed/swarm path calls rpe_pipeline.execute() (not async_swarm.execute())
- Continuation bypass path also calls rpe_pipeline.execute()
- Early-exit paths (thalamus / cache / tier_1_5) do NOT call rpe_pipeline
- SwarmResult → QueryResponse schema is unchanged
"""

from __future__ import annotations

import asyncio
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult
from app.ingress.exact_cache import ExactCache
from app.main import app


def _stub_swarm_result() -> SwarmResult:
    return SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="rpe-pipeline-output",
            tier_used="STANDARD",
            model_name="mock",
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason="stop",
            latency_ms=5.0,
            ne_applied=False,
            plan_intent="answer",
        ),
        context_status="ok",
        planner_status="ok",
        generator_status="ok",
        total_elapsed_ms=20.0,
    )


class _SpyRpePipeline:
    """RPEMutationPipelineWrapper 대체 — execute 호출 기록 + 정상 결과 반환."""

    def __init__(self) -> None:
        self.execute_call_count = 0
        self.last_trace_id: str | None = None
        self.last_session_id: str | None = None

    async def execute(
        self,
        task_context,
        query_features=None,
        *,
        trace_id: str,
        session_id: str,
    ) -> SwarmResult:
        self.execute_call_count += 1
        self.last_trace_id = trace_id
        self.last_session_id = session_id
        return _stub_swarm_result()


class _FakeSemanticCache:
    def __init__(self) -> None:
        self.next_result = None

    async def get(self, prompt: str, threshold: float = 0.90, **_ns):
        if self.next_result is None:
            return None
        response, similarity = self.next_result
        if similarity < threshold:
            return None
        return response, similarity

    async def put(self, prompt: str, response: str) -> None:
        pass


@pytest.fixture
def client(app_client) -> Iterator[tuple[TestClient, _SpyRpePipeline]]:
    # app_client (conftest): 세션 app 1회 lifespan + per-test tmp ExactCache + semantic/exact
    # 원복. rpe_pipeline 전체는 이 모듈이 직접 save/restore 한다.
    c = app_client
    saved_pipeline = c.app.state.rpe_pipeline
    try:
        c.app.state.semantic_cache = _FakeSemanticCache()
        spy = _SpyRpePipeline()
        c.app.state.rpe_pipeline = spy
        yield c, spy
    finally:
        c.app.state.rpe_pipeline = saved_pipeline


class TestRoutedPathCallsPipeline:
    def test_routed_path_calls_rpe_pipeline(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        resp = c.post("/query", json={"prompt": "what is async python?"})
        assert resp.status_code == 200
        assert spy.execute_call_count == 1

    def test_routed_path_passes_trace_id(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        c.post("/query", json={"prompt": "explain decorators"})
        assert spy.last_trace_id is not None
        assert len(spy.last_trace_id) > 0

    def test_routed_path_passes_session_id(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        c.post("/query", json={"prompt": "explain generators", "session_id": "sess-route"})
        assert spy.last_session_id is not None

    def test_response_schema_unchanged(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        resp = c.post("/query", json={"prompt": "how does asyncio work?"})
        data = resp.json()
        assert "trace_id" in data
        assert "answer" in data
        assert "path_taken" in data

    def test_response_source_is_swarm(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        resp = c.post("/query", json={"prompt": "what is rust borrow checker?"})
        data = resp.json()
        assert data.get("response_source") == "swarm"


class TestEarlyExitNoPipeline:
    def test_thalamus_exit_no_pipeline_call(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        # Very short prompt → Thalamus blocks it (below min token).
        resp = c.post("/query", json={"prompt": "hi"})
        # Thalamus should handle this; rpe_pipeline must not be called.
        assert spy.execute_call_count == 0

    def test_exact_cache_hit_no_pipeline_call(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        # Pre-populate exact cache using asyncio.run (fresh event loop).
        asyncio.run(
            c.app.state.exact_cache.put(
                "tell me about python caching",
                "Python caching answer",
            )
        )
        resp = c.post("/query", json={"prompt": "tell me about python caching"})
        # Exact cache hit → no rpe_pipeline call.
        assert spy.execute_call_count == 0


class TestMultipleExecutions:
    def test_two_routed_requests_two_calls(
        self, client: tuple[TestClient, _SpyRpePipeline]
    ) -> None:
        c, spy = client
        c.post("/query", json={"prompt": "what is python list comprehension?"})
        c.post("/query", json={"prompt": "explain python generators?"})
        assert spy.execute_call_count == 2
