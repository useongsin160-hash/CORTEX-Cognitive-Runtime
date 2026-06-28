"""LiveLLMClient 슬롯 기반 배선 테스트 (V4). 네트워크 0.

가짜 어댑터(ProtocolAdapter 구현)로 호출 인자를 가로채고, 실제 어댑터+MockTransport 로
통합 경로도 검증한다. 슬롯은 tmp tier_slots.json + TIER_SLOTS_CONFIG_PATH env 로 격리.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.model_tier import ModelTier
from app.core.slot_registry import TierSlot
from app.execution.factory import get_llm_client
from app.execution.live_llm_client import LiveLLMClient, UnsupportedProtocolError
from app.execution.llm_client import LLMResult
from app.execution.mock_llm_client import MockLLMClient
from app.execution.params import GenerationParams
from app.execution.protocol_adapters import ProtocolAdapter
from app.execution.protocol_adapters.openai_compatible import OpenAICompatibleAdapter

_KEY_ENVS = [f"CORTEX_SLOT_{t.name}_KEY" for t in ModelTier]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TIER_SLOTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    for e in _KEY_ENVS:
        monkeypatch.delenv(e, raising=False)


def _write_config(tmp_path, *, protocol="fake_proto", **per_tier) -> str:
    data = {}
    for tier in ModelTier:
        slot = {
            "base_url": "https://api.example.com",
            "api_key_env": f"CORTEX_SLOT_{tier.name}_KEY",
            "protocol": protocol,
            "model": f"cfg-{tier.name.lower()}",
        }
        slot.update(per_tier.get(tier.name, {}))
        data[tier.name] = slot
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


class _FakeAdapter(ProtocolAdapter):
    """호출 인자를 기록하고 tier_used="" 인 LLMResult 를 반환(실 어댑터 모방)."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    async def generate(self, prompt, slot, api_key, params) -> LLMResult:
        self.calls.append({"prompt": prompt, "slot": slot, "api_key": api_key, "params": params})
        return LLMResult(
            text="fake-response",
            tier_used="",            # V4 LiveLLMClient 가 확정해야 함
            model_name=slot.model,
            prompt_tokens=3,
            completion_tokens=2,
            finish_reason="stop",
            mode="live",
            latency_ms=1.0,
            params_used=params,
        )


# ── 슬롯 → 어댑터 → tier_used 확정 ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_slot_to_adapter_and_tier_used_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_config(tmp_path))
    monkeypatch.setenv("CORTEX_SLOT_STANDARD_KEY", "k-standard")
    fake = _FakeAdapter()
    client = LiveLLMClient(adapters={"fake_proto": fake})

    result = await client.generate("hello", ModelTier.STANDARD, GenerationParams())

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["prompt"] == "hello"
    assert isinstance(call["slot"], TierSlot)
    assert call["slot"].model == "cfg-standard"
    assert call["api_key"] == "k-standard"
    # tier_used 가 어댑터의 ""에서 tier.name 으로 확정
    assert result.tier_used == "STANDARD"
    assert result.model_name == "cfg-standard"
    assert result.text == "fake-response"


@pytest.mark.asyncio
async def test_vendor_arg_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_config(tmp_path))
    monkeypatch.setenv("CORTEX_SLOT_HEAVY_KEY", "k-heavy")
    fake = _FakeAdapter()
    client = LiveLLMClient(adapters={"fake_proto": fake})

    r1 = await client.generate("p", ModelTier.HEAVY, GenerationParams(), vendor="anthropic")
    r2 = await client.generate("p", ModelTier.HEAVY, GenerationParams(), vendor="whatever")
    assert r1.model_name == r2.model_name == "cfg-heavy"
    assert r1.tier_used == r2.tier_used == "HEAVY"


# ── api_key 전달 (무인증 슬롯 → None) ───────────────────────────────────────
@pytest.mark.asyncio
async def test_no_auth_slot_passes_none_key(tmp_path, monkeypatch):
    cfg = _write_config(
        tmp_path,
        LIGHTWEIGHT={"api_key_env": None, "allow_empty_api_key": True},
    )
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", cfg)
    fake = _FakeAdapter()
    client = LiveLLMClient(adapters={"fake_proto": fake})

    await client.generate("p", ModelTier.LIGHTWEIGHT, GenerationParams())
    assert fake.calls[0]["api_key"] is None


# ── 미지원 protocol → 명확한 에러 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_unsupported_protocol_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_config(tmp_path, protocol="unknown_x"))
    monkeypatch.setenv("CORTEX_SLOT_STANDARD_KEY", "k")
    client = LiveLLMClient(adapters={"fake_proto": _FakeAdapter()})
    with pytest.raises(UnsupportedProtocolError) as exc:
        await client.generate("p", ModelTier.STANDARD, GenerationParams())
    assert "unknown_x" in str(exc.value)


# ── 통합: 실제 OpenAI 호환 어댑터 + MockTransport (네트워크 0) ──────────────
@pytest.mark.asyncio
async def test_integration_real_adapter_with_mock_transport(tmp_path, monkeypatch):
    cfg = _write_config(
        tmp_path,
        STANDARD={"protocol": "openai_compatible", "model": "gpt-x"},
    )
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", cfg)
    monkeypatch.setenv("CORTEX_SLOT_STANDARD_KEY", "secret-not-leaked")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "live-ish"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        })

    adapter = OpenAICompatibleAdapter(transport=httpx.MockTransport(handler))
    client = LiveLLMClient(adapters={"openai_compatible": adapter})

    result = await client.generate("hi", ModelTier.STANDARD, GenerationParams())

    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer secret-not-leaked"
    assert result.text == "live-ish"
    assert result.tier_used == "STANDARD"
    assert result.model_name == "gpt-x"
    assert (result.prompt_tokens, result.completion_tokens) == (5, 3)


# ── factory: 기본 mock 불변 ─────────────────────────────────────────────────
def test_factory_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    assert isinstance(get_llm_client(), MockLLMClient)


def test_factory_live_returns_live_client(monkeypatch):
    monkeypatch.setenv("CORTEX_LLM_MODE", "live")
    assert isinstance(get_llm_client(), LiveLLMClient)
