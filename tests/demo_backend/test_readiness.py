"""demo_backend /demo/readiness 정직화 테스트.

core /health(llm_mode + slots_ready)를 그대로 중계한다. demo 는 키/벤더/모드를
자체 판단하지 않는다. 거짓 ready·거짓 not-ready 둘 다 버그다.

격리: FakeCortex 로 core 호출을 대체 — 실 네트워크·실 LLM·chromadb 0.
lifespan 미기동(무거운 CortexClient 미생성) — app.state 를 직접 주입한다.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from demo_backend.cortex_client import CortexClient
from demo_backend.main import create_demo_app
from demo_backend.settings import get_demo_settings


class _FakeCortex:
    """CortexClient.health() 더블. readiness 는 query/get_trace 를 쓰지 않는다."""

    def __init__(self, health_payload):
        self._payload = health_payload

    async def health(self):
        return self._payload


def _client(health_payload) -> TestClient:
    app = create_demo_app()
    # lifespan 을 띄우지 않으므로 readiness 가 읽는 state 를 직접 주입한다.
    app.state.settings = get_demo_settings()
    app.state.cortex = _FakeCortex(health_payload)
    return TestClient(app)  # `with` 없이 — lifespan 미기동


def _ok(llm_mode, slots_ready):
    return {"status": "ok", "version": "0.1.0", "llm_mode": llm_mode, "slots_ready": slots_ready}


# ── core mock → live-not-ready ──────────────────────────────────────────────
def test_core_mock_is_not_live_ready():
    r = _client(_ok("mock", True)).get("/demo/readiness").json()
    assert r["cortex_reachable"] is True
    assert r["llm_live_enabled"] is False
    assert r["can_run_live_llm"] is False  # mock → live 게이트 닫힘
    assert r["demo_mode"] == "stub"
    assert r["slots_ready"] is True  # core 값 그대로 중계(live 아님과 독립)


# ── core live + slots_ready=true → live-ready ───────────────────────────────
def test_core_live_with_slots_ready_is_live_ready():
    r = _client(_ok("live", True)).get("/demo/readiness").json()
    assert r["llm_live_enabled"] is True
    assert r["slots_ready"] is True
    assert r["can_run_live_llm"] is True
    assert r["demo_mode"] == "live"
    assert r["can_run_query"] is True


# ── core live + slots_ready=false → not-ready(슬롯 미준비) ───────────────────
def test_core_live_but_slots_not_ready_is_not_ready():
    r = _client(_ok("live", False)).get("/demo/readiness").json()
    assert r["llm_live_enabled"] is True
    assert r["slots_ready"] is False
    assert r["can_run_live_llm"] is False  # 슬롯 미준비 → live 게이트 닫힘
    assert r["demo_mode"] == "stub"


# ── core 도달 실패 → graceful not-ready (500 아님) ──────────────────────────
def test_core_unreachable_is_graceful_not_ready():
    resp = _client(None).get("/demo/readiness")  # health() → None
    assert resp.status_code == 200
    r = resp.json()
    assert r["cortex_reachable"] is False
    assert r["can_run_query"] is False
    assert r["llm_live_enabled"] is False
    assert r["slots_ready"] is False
    assert r["can_run_live_llm"] is False
    assert r["demo_mode"] == "stub"


# ── 구버전 core(필드 부재) → 방어적 not-ready ───────────────────────────────
def test_core_health_missing_fields_defaults_not_ready():
    r = _client({"status": "ok", "version": "0.1.0"}).get("/demo/readiness").json()
    assert r["llm_live_enabled"] is False
    assert r["slots_ready"] is False
    assert r["can_run_live_llm"] is False
    assert r["demo_mode"] == "stub"


# ── 키 값·키 env 이름·벤더명이 응답 어디에도 없다 ──────────────────────────
def test_response_leaks_no_key_env_name_or_vendor():
    body = _client(_ok("live", True)).get("/demo/readiness").text.lower()
    for forbidden in (
        "anthropic_api_key", "cortex_gemini_api_key", "cortex_slot_",
        "api_key", "sk-",
        "gemini", "anthropic", "google", "openai",  # 벤더명
    ):
        assert forbidden not in body


# ── client 레벨: httpx 에러를 None 으로 흡수(500 유발 안 함) ─────────────────
def test_cortex_client_health_swallows_httpx_errors():
    def _raise(request):
        raise httpx.ConnectError("boom", request=request)

    async def _run():
        client = CortexClient(
            "http://127.0.0.1:8000", transport=httpx.MockTransport(_raise)
        )
        try:
            return await client.health()
        finally:
            await client.aclose()

    assert asyncio.run(_run()) is None
