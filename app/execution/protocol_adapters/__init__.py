"""Protocol adapters — 봉투 양식별 플러그인 레지스트리.

ADAPTERS 는 protocol 이름 → 어댑터 인스턴스의 dict 다. 디스패치는 dict 조회로만
이뤄지며 if/else 벤더 체인은 쓰지 않는다(설계 5-3). 새 양식 추가 = ADAPTERS 에
한 줄 등록. 어댑터 간 서열은 없다(설계 0-3).

V4 에서 LiveLLMClient 가 `ADAPTERS[slot.protocol]` 로 어댑터를 선택해 호출한다.
V3 시점에는 만들어 두기만 하고 아무도 호출하지 않는다.
"""
from __future__ import annotations

from app.execution.protocol_adapters.anthropic import AnthropicAdapter
from app.execution.protocol_adapters.base import AdapterError, ProtocolAdapter
from app.execution.protocol_adapters.google import GoogleAdapter
from app.execution.protocol_adapters.openai_compatible import OpenAICompatibleAdapter

ADAPTERS: dict[str, ProtocolAdapter] = {
    "openai_compatible": OpenAICompatibleAdapter(),
    "anthropic": AnthropicAdapter(),
    "google": GoogleAdapter(),
}

__all__ = [
    "ADAPTERS",
    "ProtocolAdapter",
    "AdapterError",
    "OpenAICompatibleAdapter",
    "AnthropicAdapter",
    "GoogleAdapter",
]
