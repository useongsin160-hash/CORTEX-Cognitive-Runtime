"""Protocol adapter 테스트 (V3).

네트워크 호출 절대 금지 — httpx.MockTransport 만 사용한다. handler 가 요청을
가로채 URL/헤더/body 를 검사하고 canned 응답을 반환한다.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.slot_registry import TierSlot
from app.execution.params import GenerationParams
from app.execution.protocol_adapters import (
    ADAPTERS,
    AdapterError,
    AnthropicAdapter,
    GoogleAdapter,
    OpenAICompatibleAdapter,
    ProtocolAdapter,
)

SECRET = "super-secret-key-123"  # 절대 로그/예외에 새면 안 되는 sentinel


def _slot(protocol: str, **overrides) -> TierSlot:
    base = {
        "base_url": "https://api.example.com",
        "api_key_env": "CORTEX_SLOT_X_KEY",
        "protocol": protocol,
        "model": "test-model",
    }
    base.update(overrides)
    return TierSlot(**base)


class _Capture:
    """MockTransport handler — 요청을 잡아두고 canned 응답을 돌려준다."""

    def __init__(self, *, status: int = 200, json_body: dict | None = None):
        self.request: httpx.Request | None = None
        self._status = status
        self._json = json_body if json_body is not None else {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.request = request
        return httpx.Response(self._status, json=self._json)

    @property
    def body(self) -> dict:
        assert self.request is not None
        return json.loads(self.request.content)


def _transport(cap: _Capture) -> httpx.MockTransport:
    return httpx.MockTransport(cap)


# ── openai_compatible ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_openai_request_shape_and_normalization():
    cap = _Capture(json_body={
        "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    })
    adapter = OpenAICompatibleAdapter(transport=_transport(cap))
    slot = _slot("openai_compatible", model="gpt-x")
    params = GenerationParams(temperature=0.3, top_p=0.8, max_tokens=256)

    result = await adapter.generate("hello", slot, SECRET, params)

    assert str(cap.request.url).endswith("/chat/completions")
    assert cap.request.headers["Authorization"] == f"Bearer {SECRET}"
    assert cap.body["model"] == "gpt-x"
    assert cap.body["messages"] == [{"role": "user", "content": "hello"}]
    assert cap.body["temperature"] == 0.3
    assert cap.body["top_p"] == 0.8
    assert cap.body["max_tokens"] == 256
    # 정규화된 LLMResult
    assert result.text == "hi there"
    assert result.finish_reason == "stop"
    assert result.mode == "live"
    assert result.model_name == "gpt-x"
    assert (result.prompt_tokens, result.completion_tokens) == (11, 7)


@pytest.mark.asyncio
async def test_openai_top_k_gated_by_supports_flag():
    # supports_top_k=False (기본) → top_k 미전송
    cap_off = _Capture(json_body={"choices": [{"message": {"content": "x"}}]})
    await OpenAICompatibleAdapter(transport=_transport(cap_off)).generate(
        "p", _slot("openai_compatible"), SECRET, GenerationParams(top_k=99))
    assert "top_k" not in cap_off.body

    # supports_top_k=True → 전송
    cap_on = _Capture(json_body={"choices": [{"message": {"content": "x"}}]})
    await OpenAICompatibleAdapter(transport=_transport(cap_on)).generate(
        "p", _slot("openai_compatible", supports_top_k=True), SECRET, GenerationParams(top_k=99))
    assert cap_on.body["top_k"] == 99


@pytest.mark.asyncio
async def test_openai_no_auth_header_when_key_none():
    cap = _Capture(json_body={"choices": [{"message": {"content": "x"}}]})
    slot = _slot("openai_compatible", api_key_env=None, allow_empty_api_key=True)
    await OpenAICompatibleAdapter(transport=_transport(cap)).generate(
        "p", slot, None, GenerationParams())
    assert "authorization" not in {k.lower() for k in cap.request.headers}


@pytest.mark.asyncio
async def test_openai_finish_reason_length():
    cap = _Capture(json_body={
        "choices": [{"message": {"content": "x"}, "finish_reason": "length"}],
    })
    result = await OpenAICompatibleAdapter(transport=_transport(cap)).generate(
        "p", _slot("openai_compatible"), SECRET, GenerationParams())
    assert result.finish_reason == "length"


# ── anthropic ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_anthropic_request_shape_and_normalization():
    cap = _Capture(json_body={
        "content": [{"type": "text", "text": "claude says hi"}],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 20, "output_tokens": 5},
    })
    adapter = AnthropicAdapter(transport=_transport(cap))
    slot = _slot("anthropic", model="claude-x")
    result = await adapter.generate("hello", slot, SECRET, GenerationParams(max_tokens=512))

    assert str(cap.request.url).endswith("/v1/messages")
    assert cap.request.headers["x-api-key"] == SECRET
    assert cap.request.headers["anthropic-version"] == "2023-06-01"
    assert cap.body["max_tokens"] == 512
    assert cap.body["messages"] == [{"role": "user", "content": "hello"}]
    assert result.text == "claude says hi"
    assert result.finish_reason == "length"  # max_tokens → length
    assert (result.prompt_tokens, result.completion_tokens) == (20, 5)
    assert result.model_name == "claude-x"


# ── google ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_google_request_shape_and_key_not_in_url():
    cap = _Capture(json_body={
        "candidates": [{
            "content": {"parts": [{"text": "gemini says hi"}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4},
    })
    adapter = GoogleAdapter(transport=_transport(cap))
    slot = _slot("google", model="gemini-x", base_url="https://gen.googleapis.com")
    result = await adapter.generate("hello", slot, SECRET, GenerationParams(top_p=0.95))

    url = str(cap.request.url)
    assert ":generateContent" in url
    assert "gemini-x" in url
    # 키는 헤더로만 — URL/쿼리에 절대 미포함
    assert SECRET not in url
    assert cap.request.url.query == b""
    assert cap.request.headers["x-goog-api-key"] == SECRET
    assert cap.body["contents"] == [{"parts": [{"text": "hello"}]}]
    assert cap.body["generationConfig"]["topP"] == 0.95
    assert "topK" not in cap.body["generationConfig"]  # supports_top_k=False
    assert result.text == "gemini says hi"
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens) == (9, 4)


# ── 공통: usage 없는 응답도 LLMResult 생성 ──────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls,protocol,body", [
    (OpenAICompatibleAdapter, "openai_compatible", {"choices": [{"message": {"content": "abcd"}}]}),
    (AnthropicAdapter, "anthropic", {"content": [{"type": "text", "text": "abcd"}]}),
    (GoogleAdapter, "google", {"candidates": [{"content": {"parts": [{"text": "abcd"}]}}]}),
])
async def test_usage_missing_estimates(adapter_cls, protocol, body):
    cap = _Capture(json_body=body)
    slot = _slot(protocol)  # usage_strategy 기본 "provider" → 누락 시 estimate
    prompt = "a fairly long prompt to estimate tokens from"
    result = await adapter_cls(transport=_transport(cap)).generate(
        prompt, slot, SECRET, GenerationParams())
    assert result.prompt_tokens == len(prompt) // 4
    assert result.completion_tokens == len("abcd") // 4
    assert result.text == "abcd"


@pytest.mark.asyncio
async def test_usage_strategy_zero():
    cap = _Capture(json_body={"choices": [{"message": {"content": "abcd"}}]})
    slot = _slot("openai_compatible", usage_strategy="zero")
    result = await OpenAICompatibleAdapter(transport=_transport(cap)).generate(
        "prompt", slot, SECRET, GenerationParams())
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)


# ── 공통: api_key 가 예외 메시지에 미노출 ───────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls,protocol", [
    (OpenAICompatibleAdapter, "openai_compatible"),
    (AnthropicAdapter, "anthropic"),
    (GoogleAdapter, "google"),
])
async def test_http_error_raises_adapter_error_without_key(adapter_cls, protocol):
    cap = _Capture(status=500, json_body={"error": "boom"})
    with pytest.raises(AdapterError) as exc:
        await adapter_cls(transport=_transport(cap)).generate(
            "p", _slot(protocol), SECRET, GenerationParams())
    msg = str(exc.value)
    assert SECRET not in msg
    assert "500" in msg


@pytest.mark.asyncio
async def test_transport_error_raises_adapter_error_without_key():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AdapterError) as exc:
        await OpenAICompatibleAdapter(transport=httpx.MockTransport(boom)).generate(
            "p", _slot("openai_compatible"), SECRET, GenerationParams())
    assert SECRET not in str(exc.value)


# ── ADAPTERS 레지스트리 ──────────────────────────────────────────────────────
def test_adapters_registry_keys_and_types():
    assert set(ADAPTERS.keys()) == {"openai_compatible", "anthropic", "google"}
    for adapter in ADAPTERS.values():
        assert isinstance(adapter, ProtocolAdapter)
