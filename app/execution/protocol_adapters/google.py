"""Google Gemini generateContent 양식 어댑터.

POST {base_url}/v1beta/models/{model}:generateContent

인증은 이 어댑터의 책임이다(설계 5-4). 키를 쿼리파라미터(?key=)가 아닌
x-goog-api-key 헤더로 전달해 키가 URL/로그에 남지 않게 한다. topK 는
slot.supports_top_k=True 일 때만 전송(설계 6).
"""
from __future__ import annotations

import time

from app.core.slot_registry import TierSlot
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams
from app.execution.protocol_adapters.base import ProtocolAdapter


def _normalize_finish_reason(raw: str | None) -> str:
    if raw == "MAX_TOKENS":
        return "length"
    return "stop"


class GoogleAdapter(ProtocolAdapter):
    async def generate(
        self,
        prompt: str,
        slot: TierSlot,
        api_key: str | None,
        params: GenerationParams,
    ) -> LLMResult:
        url = f"{slot.base_url.rstrip('/')}/v1beta/models/{slot.model}:generateContent"
        headers = {"Content-Type": "application/json"}
        if api_key:
            # 키는 쿼리가 아니라 헤더로 — URL/로그 노출 방지.
            headers["x-goog-api-key"] = api_key

        generation_config: dict = {
            "temperature": params.temperature,
            "topP": params.top_p,
            "maxOutputTokens": params.max_tokens,
        }
        if slot.supports_top_k:
            generation_config["topK"] = params.top_k
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

        start = time.perf_counter()
        data = await self._post_json(slot=slot, url=url, headers=headers, json_body=body)
        latency_ms = (time.perf_counter() - start) * 1000.0

        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = ""
        for part in parts:
            if isinstance(part, dict) and "text" in part:
                text = part.get("text") or ""
                break
        finish_reason = _normalize_finish_reason(candidate.get("finishReason"))

        usage_obj = data.get("usageMetadata")
        provider_usage = (
            (
                int(usage_obj.get("promptTokenCount", 0)),
                int(usage_obj.get("candidatesTokenCount", 0)),
            )
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
