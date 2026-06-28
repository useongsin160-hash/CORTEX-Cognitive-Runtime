"""Anthropic Messages 양식 어댑터.

POST {base_url}/v1/messages — x-api-key 헤더 인증, anthropic-version 헤더 필수.
top_k 는 slot.supports_top_k=True 일 때만 전송(설계 6).
"""
from __future__ import annotations

import time

from app.core.slot_registry import TierSlot
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams
from app.execution.protocol_adapters.base import ProtocolAdapter

_ANTHROPIC_VERSION = "2023-06-01"


def _normalize_finish_reason(raw: str | None) -> str:
    if raw == "max_tokens":
        return "length"
    return "stop"


class AnthropicAdapter(ProtocolAdapter):
    async def generate(
        self,
        prompt: str,
        slot: TierSlot,
        api_key: str | None,
        params: GenerationParams,
    ) -> LLMResult:
        url = f"{slot.base_url.rstrip('/')}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        if api_key:
            headers["x-api-key"] = api_key

        body: dict = {
            "model": slot.model,
            "max_tokens": params.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": params.temperature,
            "top_p": params.top_p,
        }
        if slot.supports_top_k:
            body["top_k"] = params.top_k

        start = time.perf_counter()
        data = await self._post_json(slot=slot, url=url, headers=headers, json_body=body)
        latency_ms = (time.perf_counter() - start) * 1000.0

        blocks = data.get("content") or []
        text = ""
        for block in blocks:
            if isinstance(block, dict) and block.get("type", "text") == "text":
                text = block.get("text") or ""
                break
        finish_reason = _normalize_finish_reason(data.get("stop_reason"))

        usage_obj = data.get("usage")
        provider_usage = (
            (int(usage_obj.get("input_tokens", 0)), int(usage_obj.get("output_tokens", 0)))
            if isinstance(usage_obj, dict)
            else None
        )
        usage = self._normalize_usage(provider_usage, slot=slot, prompt=prompt, text=text)

        return self._build_result(
            text=text,
            finish_reason=finish_reason,
            usage=usage,
            slot=slot,
            params=params,
            latency_ms=latency_ms,
        )
