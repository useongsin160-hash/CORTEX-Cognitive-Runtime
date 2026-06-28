"""실제 LLM API 호출 클라이언트 — Tier Slot Registry 기반 (V4).

흐름 (설계 docs/TIER_SLOT_REGISTRY_DESIGN.md v0.3 §5-2):
    tier → get_slot(tier) → get_slot_api_key(tier)
         → ADAPTERS[slot.protocol] → adapter.generate(prompt, slot, api_key, params)
         → LLMResult (tier_used 를 tier.name 으로 확정)

protocol 디스패치는 ADAPTERS dict 조회로만 이뤄진다(if/else 벤더 체인 없음). 슬롯 선택은
오직 tier 로 한다 — generate 의 vendor 인자는 legacy 이며 무시된다(설계 4-3).

mock 모드는 이 클라이언트를 쓰지 않는다(factory 가 MockLLMClient 반환). live 모드 +
설정 파일 부재 시 get_slot 경유 LiveModeFallbackError(NO-GO, 설계 4-5)가 올라온다.
API key 값은 어댑터 헤더 구성 시점에만 쓰이며 로그/예외에 노출하지 않는다.
"""
from __future__ import annotations

from app.core.model_tier import ModelTier
from app.core.slot_registry import get_slot, get_slot_api_key
from app.execution.llm_client import LLMResult
from app.execution.params import GenerationParams
from app.execution.protocol_adapters import ADAPTERS, ProtocolAdapter


class UnsupportedProtocolError(Exception):
    """슬롯의 protocol 에 대응하는 어댑터가 ADAPTERS 에 없다 (설계 4-1)."""


class LiveLLMClient:
    """실제 LLM API 호출 클라이언트 (슬롯 기반).

    Args:
        default_vendor: legacy 보관용 — 슬롯 선택에 사용하지 않는다.
        adapters: protocol→어댑터 매핑. 기본은 전역 ADAPTERS. 테스트는 가짜/MockTransport
            어댑터 dict 를 주입해 전역 오염과 네트워크 호출을 피한다.
    """

    def __init__(
        self,
        *,
        default_vendor: str | None = None,
        adapters: dict[str, ProtocolAdapter] | None = None,
    ) -> None:
        self._default_vendor = default_vendor
        self._adapters = adapters if adapters is not None else ADAPTERS

    async def generate(
        self,
        prompt: str,
        tier: ModelTier,
        params: GenerationParams,
        vendor: str | None = None,
    ) -> LLMResult:
        # vendor 는 legacy — 무시. 슬롯은 tier 로만 선택한다.
        slot = get_slot(tier)
        api_key = get_slot_api_key(tier)

        adapter = self._adapters.get(slot.protocol)
        if adapter is None:
            raise UnsupportedProtocolError(
                f"protocol '{slot.protocol}' has no adapter "
                f"(known: {sorted(self._adapters)})"
            )

        result = await adapter.generate(prompt, slot, api_key, params)
        # 어댑터는 tier 정체를 모르므로 tier_used 를 비워둔다 — 여기서 확정한다.
        return result.model_copy(update={"tier_used": tier.name})
