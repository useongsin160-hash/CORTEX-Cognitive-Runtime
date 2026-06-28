"""OpenAI-compatible Chat Completions 어댑터.

POST {base_url}/chat/completions — messages 형식. OpenAI 및 호환 게이트웨이
(로컬 LLM 서버 등)에서 널리 쓰이는 양식이다. top_k 는 표준 필드가 아니므로
slot.supports_top_k=True 일 때만 전송한다(설계 6).
"""
from __future__ import annotations

import time

from app.core.slot_registry import TierSlot
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams
from app.execution.protocol_adapters.base import ProtocolAdapter


def _normalize_finish_reason(raw: str | None) -> str:
    if raw == "length":
        return "length"
    return "stop"


class OpenAICompatibleAdapter(ProtocolAdapter):
    async def generate(
        self,
        prompt: str,
        slot: TierSlot,
        api_key: str | None,
        params: GenerationParams,
    ) -> LLMResult:
        url = f"{slot.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict = {
            "model": slot.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
        }
        if slot.supports_top_k:
            body["top_k"] = params.top_k

        start = time.perf_counter()
        data = await self._post_json(slot=slot, url=url, headers=headers, json_body=body)
        latency_ms = (time.perf_counter() - start) * 1000.0

        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        finish_reason = _normalize_finish_reason(choice.get("finish_reason"))

        usage_obj = data.get("usage")
        provider_usage = (
            (int(usage_obj.get("prompt_tokens", 0)), int(usage_obj.get("completion_tokens", 0)))
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
