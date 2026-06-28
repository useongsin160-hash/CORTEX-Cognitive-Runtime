"""core /health read-only 상태 노출 테스트.

- llm_mode + slots_ready 를 그대로 중계(텔레메트리, 비파괴 additive 필드).
- 로그 비오염: readiness 폴링 경로가 get_spinal_logger(SpinalLogger trace/RPE
  관측)를 절대 호출하지 않음을 단언한다.

핸들러를 직접 호출한다 — 무거운 app(임베더/chromadb) 빌드 없이 검증.
slots_ready 는 monkeypatch 로 격리해 health() 가 값을 충실히 중계하는지만 본다.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.api.routes as routes


def _request(state: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_health_relays_live_and_slots_ready(monkeypatch):
    monkeypatch.setattr(routes, "compute_slots_ready", lambda: True)
    resp = asyncio.run(routes.health(_request(SimpleNamespace(llm_mode="live"))))
    assert resp.status == "ok"
    assert resp.version == "0.1.0"
    assert resp.llm_mode == "live"
    assert resp.slots_ready is True


def test_health_relays_mock_and_not_ready(monkeypatch):
    monkeypatch.setattr(routes, "compute_slots_ready", lambda: False)
    resp = asyncio.run(routes.health(_request(SimpleNamespace(llm_mode="mock"))))
    assert resp.llm_mode == "mock"
    assert resp.slots_ready is False


def test_health_defaults_llm_mode_when_state_bare(monkeypatch):
    # llm_mode 미설정 state(레거시/베어) → _state_llm_mode 기본 'mock'.
    monkeypatch.setattr(routes, "compute_slots_ready", lambda: False)
    resp = asyncio.run(routes.health(_request(SimpleNamespace())))
    assert resp.llm_mode == "mock"


def test_health_is_log_free(monkeypatch):
    # /health 는 인지 파이프라인 바깥의 순수 조회 — SpinalLogger 를 만지지 않는다.
    def _boom():
        raise AssertionError("get_spinal_logger must not be called on /health")

    monkeypatch.setattr(routes, "get_spinal_logger", _boom)
    monkeypatch.setattr(routes, "compute_slots_ready", lambda: True)
    resp = asyncio.run(routes.health(_request(SimpleNamespace(llm_mode="live"))))
    assert resp.slots_ready is True  # 도달했고(예외 없음), 로거 미호출
