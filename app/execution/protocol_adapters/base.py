"""ProtocolAdapter — 봉투 양식(protocol)별 어댑터의 공통 추상 인터페이스.

설계: docs/TIER_SLOT_REGISTRY_DESIGN.md v0.3 (§0-3, §4-7, §5-3, §6).

각 어댑터는 TierSlot 한 칸을 받아 그 protocol 규격대로 httpx 로 실제 API 를 호출하고,
응답을 공통 LLMResult 로 정규화한다. 어댑터는 평등한 플러그인이며 서열이 없다.
SDK 없이 httpx 직접 호출만 사용한다.

V3 범위: 어댑터 자체만 구현. tier→slot 매핑과 ADAPTERS[slot.protocol] dispatch 는
V4(LiveLLMClient/factory)에서 배선된다.

보안: API key 값은 헤더 구성 시점에만 사용하며, 로그/예외 메시지/URL 에 절대 노출하지 않는다.
"""
from __future__ import annotations

import abc

import httpx

from app.core.slot_registry import TierSlot
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams


class AdapterError(Exception):
    """어댑터 전송/HTTP 오류. 메시지에 api_key·인증 헤더를 절대 포함하지 않는다."""


def _safe_url(url: httpx.URL) -> str:
    """로그/예외용 URL — 쿼리스트링 제거(인증 토큰 등 잔류 방지)."""
    return f"{url.scheme}://{url.host}{url.path}"


class ProtocolAdapter(abc.ABC):
    """봉투 양식 어댑터 추상 베이스.

    transport 는 테스트 주입용이다. 프로덕션은 None(실 네트워크), 테스트는
    httpx.MockTransport 를 주입해 네트워크 호출을 0 으로 만든다.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @abc.abstractmethod
    async def generate(
        self,
        prompt: str,
        slot: TierSlot,
        api_key: str | None,
        params: GenerationParams,
    ) -> LLMResult:
        """프롬프트를 slot 의 protocol 양식대로 호출하고 LLMResult 로 정규화한다."""
        raise NotImplementedError

    # ── 공유 헬퍼 ──────────────────────────────────────────────────────────
    def _client(self, slot: TierSlot) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=slot.timeout_seconds,
        )

    async def _post_json(
        self,
        *,
        slot: TierSlot,
        url: str,
        headers: dict[str, str],
        json_body: dict,
    ) -> dict:
        """JSON POST 후 파싱된 dict 반환. 모든 실패는 key-안전 AdapterError 로 변환.

        AdapterError 메시지에는 status code 와 쿼리 제거된 URL 만 담는다(헤더·키 제외).
        """
        try:
            async with self._client(slot) as client:
                response = await client.post(url, headers=headers, json=json_body)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(
                f"{type(self).__name__} HTTP {exc.response.status_code} "
                f"from {_safe_url(exc.request.url)}"
            ) from None
        except httpx.HTTPError as exc:
            # ConnectError/TimeoutException 등 — 원 예외 메시지에 키가 없도록 종류만 기록.
            # .request 는 미설정 시 접근하면 RuntimeError 이므로 방어적으로 추출.
            try:
                where = f" to {_safe_url(exc.request.url)}"
            except RuntimeError:
                where = ""
            raise AdapterError(
                f"{type(self).__name__} transport error ({type(exc).__name__}){where}"
            ) from None

    @staticmethod
    def _normalize_usage(
        provider_usage: tuple[int, int] | None,
        *,
        slot: TierSlot,
        prompt: str,
        text: str,
    ) -> tuple[int, int]:
        """provider usage 가 있으면 그대로, 없으면 usage_strategy 에 따라 보정 (설계 4-7).

        - provider_usage 존재 → 그대로 사용
        - usage_strategy == "zero" → (0, 0)
        - 그 외("provider" / "estimate") → 4글자≈1토큰 추정 (설계 10.2 초안 estimate)
        """
        if provider_usage is not None:
            return provider_usage
        if slot.usage_strategy == "zero":
            return (0, 0)
        return (len(prompt) // 4, len(text) // 4)

    def _build_result(
        self,
        *,
        text: str,
        finish_reason: str,
        usage: tuple[int, int],
        slot: TierSlot,
        params: GenerationParams,
        latency_ms: float,
    ) -> LLMResult:
        """정규화된 LLMResult 조립.

        tier_used 는 어댑터가 tier 정체를 모르므로 빈 문자열로 둔다. V4 의
        LiveLLMClient 가 tier.name 으로 확정한다(model_copy). model_name 은
        슬롯의 model 을 그대로 사용한다.
        """
        prompt_tokens, completion_tokens = usage
        return LLMResult(
            text=text,
            tier_used="",
            model_name=slot.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
            mode="live",
            latency_ms=latency_ms,
            params_used=params,
        )
