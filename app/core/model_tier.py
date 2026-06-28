"""ModelTier 추상화 레이어.

ModelTier 5단계는 난이도 라우팅의 고정 좌표계다(값/이름 불변).

모델명은 코드 본체에 하드코딩하지 말고 resolve_model()을 통해 접근할 것.
V2 이후 모델명의 source of truth는 **Tier Slot Registry**(app/core/slot_registry.py)다.
각 tier 슬롯의 model을 바꾸려면 config/tier_slots.json(또는 env override)만 수정한다.
"외부 API 가격/모델명 변동에 코드가 흔들리지 않는 구조" 철학의 구체 구현.

설계: docs/TIER_SLOT_REGISTRY_DESIGN.md v0.3 (§5-1: MODEL_REGISTRY 폐기, resolve_model 슬롯 위임).
"""
from __future__ import annotations

from enum import IntEnum


class ModelTier(IntEnum):
    LIGHTWEIGHT = 1
    MEDIUM = 2
    STANDARD = 3
    HEAVY = 4
    DEEP_THINKING = 5


def resolve_model(vendor: str, tier: ModelTier) -> str:
    """tier 에 매핑된 모델명을 반환한다 (Tier Slot Registry 위임).

    `vendor` 인자는 **legacy**다(설계 4-3). 슬롯 선택은 오직 tier 로만 이뤄지며
    vendor 는 무시된다 — 시그니처는 기존 호출부 호환을 위해 보존한다.

    모델명은 해당 tier 슬롯의 `model` 필드에서 온다. 설정 파일이 없고 mock 모드면
    fallback 슬롯의 model("mock-{tier}")을 반환한다(키 무요구). live 모드 + 설정
    부재 시에는 slot_registry 가 LiveModeFallbackError 를 올린다(설계 4-5 안전동작).
    """
    # 지연 import: slot_registry 가 상단에서 ModelTier 를 import 하므로 모듈
    # 로드시점 순환참조를 피하기 위해 함수 내부에서 가져온다.
    from app.core.slot_registry import get_slot

    return get_slot(tier).model
