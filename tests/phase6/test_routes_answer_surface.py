"""live LLM answer path — routed/swarm path가 Generator text를 answer로 surface.

검증 범위 (네트워크 0):
  - mock 모드 routed path: answer가 "Phase 2 stub"가 아니라 MockLLMClient text,
    answer_source="generator", llm_mode="mock", route_decision/swarm_trace 보존.
  - spy swarm: answer == generator_result.text, generator_model_name 전달.
  - generator 실패(finish_reason="error"): answer는 고정 unavailable 문구,
    answer_source="unavailable", 예외/키 문자열 미포함.
  - live no-network: LiveLLMClient(fake adapter) → answer == live text, llm_mode="live".
  - early-exit(thalamus/cache): answer_source/llm_mode = None.
  - cache 비오염: routed path가 exact/semantic cache에 write하지 않음.
"""
from __future__ import annotations

import asyncio
import json
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.model_tier import ModelTier
from app.execution.context_models import ContextAgentResult
from app.execution.factory import build_execution_swarm
from app.execution.live_llm_client import LiveLLMClient
from app.execution.llm_client import LLMResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.protocol_adapters.base import AdapterError, ProtocolAdapter
from app.execution.swarm_models import SwarmResult
from app.ingress.exact_cache import ExactCache
from app.main import app
from app.routing.neuromodulators import Norepinephrine

_ROUTED_PROMPT = "help me debug this python script with a null pointer please"


# ── 헬퍼 ────────────────────────────────────────────────────────────────────
class _ConfigurableSwarm:
    """generator_result를 설정 가능한 SwarmResult로 반환하는 spy."""

    def __init__(self, *, text="spy-generated", finish_reason="stop",
                 model_name="spy-model", generator_status="ok") -> None:
        self.text = text
        self.finish_reason = finish_reason
        self.model_name = model_name
        self.generator_status = generator_status
        self.execute_call_count = 0

    async def execute(self, task_context, query_features=None):
        self.execute_call_count += 1
        return SwarmResult(
            context_result=ContextAgentResult(),
            final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
            generator_result=GeneratorResult(
                text=self.text, tier_used="STANDARD", model_name=self.model_name,
                prompt_tokens=1, completion_tokens=1, finish_reason=self.finish_reason,
                latency_ms=1.0, ne_applied=False, plan_intent="answer",
            ),
            context_status="ok",
            planner_status="ok",
            generator_status=self.generator_status,
            total_elapsed_ms=12.0,
        )


class _EmptySemanticCache:
    """semantic cache miss + put 호출 카운트(비오염 검증용)."""

    def __init__(self) -> None:
        self.collection = None
        self.put_calls = 0

    async def get(self, prompt, threshold: float = 0.90, **_ns):
        return None

    async def put(self, prompt, response) -> None:
        self.put_calls += 1


class _FakeAdapter(ProtocolAdapter):
    """네트워크 없는 live 어댑터. raise_error=True면 AdapterError."""

    def __init__(self, *, text="LIVE-TEXT", raise_error=False) -> None:
        super().__init__()
        self.text = text
        self.raise_error = raise_error

    async def generate(self, prompt, slot, api_key, params) -> LLMResult:
        if self.raise_error:
            # 메시지에 키/슬롯 비밀 없음 — key-safe.
            raise AdapterError("FakeAdapter forced failure")
        return LLMResult(
            text=self.text, tier_used="", model_name=slot.model,
            prompt_tokens=1, completion_tokens=1, finish_reason="stop",
            mode="live", latency_ms=1.0, params_used=params,
        )


def _write_live_config(tmp_path) -> str:
    """5칸 openai_compatible, allow_empty_api_key=True (키 없이 no-network)."""
    data = {
        tier.name: {
            "base_url": "https://api.example.invalid",
            "api_key_env": None,
            "protocol": "openai_compatible",
            "model": f"live-{tier.name.lower()}",
            "allow_empty_api_key": True,
        }
        for tier in ModelTier
    }
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


@pytest.fixture
def client(app_client) -> Iterator[TestClient]:
    """app_client (conftest): 세션 app 1회 lifespan + per-test tmp ExactCache + semantic/exact
    원복. async_swarm·llm_mode·rpe_pipeline._inner_swarm 은 이 모듈이 직접 복원한다."""
    c = app_client
    saved = {
        "async_swarm": c.app.state.async_swarm,
        "llm_mode": getattr(c.app.state, "llm_mode", "mock"),
    }
    saved_inner = c.app.state.rpe_pipeline._inner_swarm
    try:
        c.app.state.semantic_cache = _EmptySemanticCache()
        yield c
    finally:
        c.app.state.async_swarm = saved["async_swarm"]
        c.app.state.llm_mode = saved["llm_mode"]
        c.app.state.rpe_pipeline._inner_swarm = saved_inner


# ── A. spy swarm: generator text → answer ───────────────────────────────────
def test_routed_answer_is_generator_text(client):
    spy = _ConfigurableSwarm(text="hello from generator", model_name="m-x")
    client.app.state.rpe_pipeline._inner_swarm = spy
    client.app.state.llm_mode = "mock"

    body = client.post("/query", json={"prompt": _ROUTED_PROMPT}).json()

    assert spy.execute_call_count == 1
    assert body["answer"] == "hello from generator"
    assert not body["answer"].startswith("Phase 2 stub")
    assert body["answer_source"] == "generator"
    assert body["llm_mode"] == "mock"
    assert body["response_source"] == "swarm"
    # 보존 불변식
    assert body["path_taken"].startswith("routed_")
    assert body["route_decision"] is not None
    assert body["selected_tier"] is not None
    assert body["swarm_trace"] is not None
    assert body["swarm_trace"]["generator_model_name"] == "m-x"


# ── A2. generator 실패 → unavailable 차단 ────────────────────────────────────
def test_routed_generator_error_is_unavailable(client):
    spy = _ConfigurableSwarm(
        text="[FALLBACK] Generator failed: SomeError",
        finish_reason="error",
        generator_status="fallback",
        model_name="fallback",
    )
    client.app.state.rpe_pipeline._inner_swarm = spy
    client.app.state.llm_mode = "live"

    body = client.post("/query", json={"prompt": _ROUTED_PROMPT}).json()

    assert body["answer"] == "[ANSWER UNAVAILABLE] generation unavailable"
    assert body["answer_source"] == "unavailable"
    assert body["llm_mode"] == "live"
    # fabricated fallback 텍스트/예외 디테일을 answer로 위장하지 않는다.
    assert "FALLBACK" not in body["answer"]
    assert "SomeError" not in body["answer"]
    # swarm_trace는 보존(telemetry로 실패를 관측 가능)
    assert body["swarm_trace"]["executed"] is True


# ── B. mock 모드 통합: 진짜 MockLLMClient text ───────────────────────────────
def test_mock_mode_surfaces_mock_text(client):
    # 기본 mock 모드 + 실제 async_swarm 사용(spy 미주입).
    client.app.state.llm_mode = "mock"
    body = client.post("/query", json={"prompt": _ROUTED_PROMPT}).json()

    assert body["response_source"] == "swarm"
    assert not body["answer"].startswith("Phase 2 stub")
    assert body["answer"].startswith("[MOCK")
    assert body["answer_source"] == "generator"
    assert body["llm_mode"] == "mock"
    assert body["swarm_trace"]["generator_model_name"] is not None


# ── C. live no-network: LiveLLMClient(fake adapter) → answer ────────────────
def test_live_mode_no_network_surfaces_live_text(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_live_config(tmp_path))
    fake = _FakeAdapter(text="LIVE-ANSWER-OK")
    live_client = LiveLLMClient(adapters={"openai_compatible": fake})
    # chroma_collection=None → context retrieval은 graceful 실패(swarm가 흡수),
    # generator는 그대로 실행되어 live text를 만든다. 네트워크 0.
    swarm = build_execution_swarm(
        chroma_collection=None,
        embedder=client.app.state.embedder,
        llm_client=live_client,
        norepinephrine=Norepinephrine(),
        plc=client.app.state.plc,
        pfc=client.app.state.pfc,
    )
    client.app.state.rpe_pipeline._inner_swarm = swarm
    client.app.state.llm_mode = "live"

    body = client.post("/query", json={"prompt": _ROUTED_PROMPT}).json()

    assert body["answer"] == "LIVE-ANSWER-OK"
    assert body["answer_source"] == "generator"
    assert body["llm_mode"] == "live"
    assert body["swarm_trace"]["generator_model_name"].startswith("live-")


def test_live_mode_no_network_adapter_error_is_unavailable(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_live_config(tmp_path))
    fake = _FakeAdapter(raise_error=True)
    live_client = LiveLLMClient(adapters={"openai_compatible": fake})
    swarm = build_execution_swarm(
        chroma_collection=None,
        embedder=client.app.state.embedder,
        llm_client=live_client,
        norepinephrine=Norepinephrine(),
        plc=client.app.state.plc,
        pfc=client.app.state.pfc,
    )
    client.app.state.rpe_pipeline._inner_swarm = swarm
    client.app.state.llm_mode = "live"

    body = client.post("/query", json={"prompt": _ROUTED_PROMPT}).json()

    assert body["answer"] == "[ANSWER UNAVAILABLE] generation unavailable"
    assert body["answer_source"] == "unavailable"
    # 예외 문구/키 미노출
    assert "FakeAdapter" not in body["answer"]
    assert "forced failure" not in body["answer"]


# ── D. early-exit: answer_source/llm_mode = None ────────────────────────────
def test_thalamus_early_exit_has_no_answer_source(client):
    body = client.post("/query", json={"prompt": "안녕"}).json()
    assert body["response_source"] == "thalamus"
    assert body["answer_source"] is None
    assert body["llm_mode"] is None


def test_exact_cache_early_exit_has_no_answer_source(client):
    prompt = "exact cached answer-surface prompt"
    asyncio.run(client.app.state.exact_cache.put(prompt, "cached-reply"))
    body = client.post("/query", json={"prompt": prompt}).json()
    assert body["response_source"] == "exact_cache"
    assert body["answer"] == "cached-reply"
    assert body["answer_source"] is None
    assert body["llm_mode"] is None


# ── E. cache 비오염: routed path가 cache write 안 함 ────────────────────────
def test_routed_path_does_not_write_caches(client):
    spy = _ConfigurableSwarm(text="no-cache-write")
    client.app.state.rpe_pipeline._inner_swarm = spy
    client.post("/query", json={"prompt": _ROUTED_PROMPT})
    # semantic cache put 호출 0건
    assert client.app.state.semantic_cache.put_calls == 0
    # exact cache에도 routed prompt가 적재되지 않음
    got = asyncio.run(client.app.state.exact_cache.get(_ROUTED_PROMPT))
    assert got is None
