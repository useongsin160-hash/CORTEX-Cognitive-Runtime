"""OVERTURE A3 — 멀티 벤더 디스패치 라우팅 입증 (네트워크 0, MockTransport).

단일 LiveLLMClient + 단일 멀티벤더 config 에서, 슬롯의 protocol 값에 따라 **올바른
실 어댑터**로 라우팅되고 어댑터별 요청 봉투(URL/헤더/body)가 맞는지 입증한다:
  google → :generateContent + x-goog-api-key,
  anthropic → /v1/messages + x-api-key + anthropic-version,
  openai_compatible → /chat/completions + Bearer.

추가 가드:
  - KNOWN_PROTOCOLS == set(ADAPTERS.keys()) (H3 drift 차단: preflight 화이트리스트와
    실 디스패치 레지스트리가 어긋나면 slots_ready 와 실 호출이 불일치).
  - 디스패치 코드에 벤더 이름 == 분기가 없음을 AST 로 정적 확인(if/else 벤더 체인 금지).

키 값은 어떤 URL/예외에도 노출되지 않는다.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

from app.core.model_tier import ModelTier
from app.execution.live_llm_client import LiveLLMClient
from app.execution.params import GenerationParams
from app.execution.protocol_adapters.anthropic import AnthropicAdapter
from app.execution.protocol_adapters.google import GoogleAdapter
from app.execution.protocol_adapters.openai_compatible import OpenAICompatibleAdapter

_KEY_ENVS = [f"CORTEX_SLOT_{t.name}_KEY" for t in ModelTier]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TIER_SLOTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    for e in _KEY_ENVS:
        monkeypatch.delenv(e, raising=False)


def _write_multivendor(tmp_path) -> str:
    """3종 protocol 혼합 config. 슬롯별 distinct base_url/model/api_key_env."""
    specs = {
        "LIGHTWEIGHT": ("openai_compatible", "m-oai"),
        "MEDIUM": ("google", "m-goog"),
        "STANDARD": ("anthropic", "m-ant"),
        "HEAVY": ("openai_compatible", "m-oai2"),
        "DEEP_THINKING": ("anthropic", "m-ant2"),
    }
    data = {}
    for tier in ModelTier:
        proto, model = specs[tier.name]
        data[tier.name] = {
            "base_url": f"https://{tier.name.lower()}.api.invalid",
            "api_key_env": f"CORTEX_SLOT_{tier.name}_KEY",
            "protocol": proto,
            "model": model,
            "allow_empty_api_key": False,
        }
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _routed_transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    """URL 양식으로 응답을 분기하는 MockTransport — 실 네트워크 0."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        url = str(request.url)
        if url.endswith("/chat/completions"):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "oai-text"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        if url.endswith("/v1/messages"):
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "ant-text"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        if ":generateContent" in url:
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": "goog-text"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            })
        return httpx.Response(404, json={"error": "unrouted"})


    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_each_protocol_routes_to_its_real_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_multivendor(tmp_path))
    monkeypatch.setenv("CORTEX_SLOT_LIGHTWEIGHT_KEY", "key-lightweight")
    monkeypatch.setenv("CORTEX_SLOT_MEDIUM_KEY", "key-medium")
    monkeypatch.setenv("CORTEX_SLOT_STANDARD_KEY", "key-standard")

    captured: list[httpx.Request] = []
    transport = _routed_transport(captured)
    # 실 어댑터 + 공유 MockTransport. 디스패치는 LiveLLMClient 가 ADAPTERS[slot.protocol].
    client = LiveLLMClient(adapters={
        "openai_compatible": OpenAICompatibleAdapter(transport=transport),
        "anthropic": AnthropicAdapter(transport=transport),
        "google": GoogleAdapter(transport=transport),
    })

    # openai_compatible 슬롯 (LIGHTWEIGHT)
    r_oai = await client.generate("p", ModelTier.LIGHTWEIGHT, GenerationParams())
    req_oai = captured[-1]
    assert str(req_oai.url).endswith("/chat/completions")
    assert req_oai.headers["Authorization"] == "Bearer key-lightweight"
    assert json.loads(req_oai.content)["model"] == "m-oai"
    assert r_oai.tier_used == "LIGHTWEIGHT"
    assert r_oai.model_name == "m-oai"
    assert r_oai.text == "oai-text"

    # google 슬롯 (MEDIUM)
    r_goog = await client.generate("p", ModelTier.MEDIUM, GenerationParams())
    req_goog = captured[-1]
    assert ":generateContent" in str(req_goog.url)
    assert "m-goog" in str(req_goog.url)
    assert req_goog.headers["x-goog-api-key"] == "key-medium"
    assert req_goog.url.query == b""  # 키는 헤더로만 — 쿼리/URL 미노출
    assert json.loads(req_goog.content)["contents"] == [{"parts": [{"text": "p"}]}]
    assert r_goog.tier_used == "MEDIUM"
    assert r_goog.model_name == "m-goog"
    assert r_goog.text == "goog-text"

    # anthropic 슬롯 (STANDARD)
    r_ant = await client.generate("p", ModelTier.STANDARD, GenerationParams())
    req_ant = captured[-1]
    assert str(req_ant.url).endswith("/v1/messages")
    assert req_ant.headers["x-api-key"] == "key-standard"
    assert req_ant.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(req_ant.content)["model"] == "m-ant"
    assert r_ant.tier_used == "STANDARD"
    assert r_ant.model_name == "m-ant"
    assert r_ant.text == "ant-text"

    # 키 값은 어떤 요청 URL 에도 새지 않는다.
    all_urls = " ".join(str(r.url) for r in captured)
    for secret in ("key-lightweight", "key-medium", "key-standard"):
        assert secret not in all_urls


def test_known_protocols_matches_adapters_registry():
    # H3 drift 가드: 두 레지스트리가 단일 진실로 합치되어야 한다.
    from app.core.slot_registry import KNOWN_PROTOCOLS
    from app.execution.protocol_adapters import ADAPTERS

    assert set(ADAPTERS.keys()) == set(KNOWN_PROTOCOLS)


# ── 정적 확인: 디스패치 코드에 벤더 == 분기 없음 ────────────────────────────
_DISPATCH_FILES = (
    "app/execution/live_llm_client.py",
    "app/execution/protocol_adapters/__init__.py",
)
_VENDOR_TOKENS = {
    "google", "anthropic", "openai", "openai_compatible", "gemini", "claude", "gpt",
}
_ROOT = Path(__file__).resolve().parents[2]


def test_no_vendor_branch_in_dispatch_code():
    """디스패치는 ADAPTERS[slot.protocol] 매핑이어야 하며, `protocol == "google"`
    류의 벤더 == 분기를 두면 안 된다(설계 5-3 if/else 체인 금지). ADAPTERS dict 의
    문자열 키는 Compare 가 아니라 Dict 리터럴이므로 이 검사에 걸리지 않는다."""
    for rel in _DISPATCH_FILES:
        tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for operand in (node.left, *node.comparators):
                    if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                        assert operand.value.lower() not in _VENDOR_TOKENS, (
                            f"{rel}:{node.lineno} compares against vendor literal "
                            f"'{operand.value}'. Dispatch must use ADAPTERS[slot.protocol], "
                            f"not an if/else vendor branch."
                        )
