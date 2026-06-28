"""Tier Slot Registry — V1 (data structures + loading/validation + key lookup).

설계: docs/TIER_SLOT_REGISTRY_DESIGN.md v0.3.

멘탈 모델: CORTEX는 회사를 고르지 않는다. ModelTier 5칸 각각에 서로 무관한
독립 API(base_url·키·protocol·model)를 꽂는 인지 라우팅 런타임이다. 슬롯끼리
무관하며 "벤더" 개념은 이 설계에 존재하지 않는다.

V1 범위: 슬롯 데이터 구조 + 로딩/5칸 검증 + 키 동적 조회까지.
  - 실제 호출(protocol 어댑터 + httpx)은 V3, live_llm_client/factory 배선은 V4.
  - 따라서 이 모듈은 네트워크 호출 코드를 포함하지 않으며, ModelTier 외에
    app 내부 모듈을 import 하지 않는다 (model_tier.py / config.py 미수정).

위험 3 (mock 보존): get_slot / load_tier_slots 는 키 없이 동작한다. 키를 요구하는
것은 오직 get_slot_api_key 뿐이며, MockLLMClient 는 이를 호출하지 않는다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.core.model_tier import ModelTier

# protocol(봉투 양식)의 V1 화이트리스트.
# 설계 4-1: protocol 의 source of truth 는 ADAPTERS 레지스트리다. 그러나 어댑터는
# V3에서 도입되므로, V1 에서는 preflight 가 "알려진 양식 이름인지"만 검사한다.
# V3 에서 이 집합은 ADAPTERS.keys() 로 대체된다. protocol 간 서열은 없다.
KNOWN_PROTOCOLS: frozenset[str] = frozenset({
    "openai_compatible",
    "anthropic",
    "google",
})

# 설정 파일 경로 환경변수 / 기본 경로. config.py 를 건드리지 않기 위해 여기서
# os.getenv 로 직접 읽는다 (V2 에서 Settings 로 이관 검토).
TIER_SLOTS_CONFIG_ENV = "TIER_SLOTS_CONFIG_PATH"
DEFAULT_TIER_SLOTS_PATH = "./config/tier_slots.json"
LLM_MODE_ENV = "CORTEX_LLM_MODE"


# ── 예외 ──────────────────────────────────────────────────────────────────
# 모든 예외 메시지는 API key 값을 절대 포함하지 않는다 (tier / env 이름만 허용).
class SlotRegistryError(Exception):
    """Tier slot registry 관련 오류의 베이스."""


class IncompleteSlotRegistryError(SlotRegistryError):
    """5칸(ModelTier) 중 일부가 누락됐거나 슬롯 구조가 유효하지 않다."""


class MissingApiKeyError(SlotRegistryError):
    """allow_empty_api_key=False 인데 api_key_env 가 없거나 env 값이 비어 있다."""


class LiveModeFallbackError(SlotRegistryError):
    """live 모드인데 tier_slots.json(또는 override)이 없어 mock fallback 으로
    실호출을 시도할 수 없다 — NO-GO (설계 4-5)."""


# ── 데이터 구조 (설계 2-1 / 2-2 그대로) ─────────────────────────────────────
class TierSlot(BaseModel):
    base_url: str
    api_key_env: str | None = None       # None 허용 (로컬/무인증 API) — 4-2
    protocol: str                         # str. ADAPTERS(V3)/KNOWN_PROTOCOLS 로 검증 — 4-1
    model: str
    allow_empty_api_key: bool = False     # 로컬/무인증용
    timeout_seconds: float = 60.0
    # capability flags (양식 같아도 서버마다 지원 다름 — 6절)
    supports_top_k: bool = False
    supports_system_prompt: bool = True
    usage_strategy: str = "provider"      # "provider" | "estimate" | "zero" (4-7)


class TierSlotRegistry(BaseModel):
    LIGHTWEIGHT: TierSlot
    MEDIUM: TierSlot
    STANDARD: TierSlot
    HEAVY: TierSlot
    DEEP_THINKING: TierSlot


# ── 로딩 / 검증 ─────────────────────────────────────────────────────────────
def _resolve_config_path(path: str | None) -> Path:
    raw = path or os.getenv(TIER_SLOTS_CONFIG_ENV) or DEFAULT_TIER_SLOTS_PATH
    return Path(raw)


def _mock_fallback_registry() -> TierSlotRegistry:
    """파일이 없을 때의 mock/display 전용 fallback (설계 4-5).

    모든 칸이 키 없이 동작하도록 allow_empty_api_key=True, api_key_env=None.
    MODEL_REGISTRY 를 참조하지 않는다 (독립 슬롯 철학 + 금지어 회피).
    live 모드에서는 이 fallback 으로 실호출을 시도하지 않는다 (load 가 차단).
    """
    slots = {
        tier.name: TierSlot(
            base_url="mock://localhost",
            api_key_env=None,
            protocol="openai_compatible",
            model=f"mock-{tier.name.lower()}",
            allow_empty_api_key=True,
        )
        for tier in ModelTier
    }
    return TierSlotRegistry(**slots)


def load_tier_slots(
    path: str | None = None,
    *,
    llm_mode: str | None = None,
) -> TierSlotRegistry:
    """tier_slots.json 을 로드하고 5칸 완전성을 검증한다.

    경로 우선순위: 인자 path → 환경변수 TIER_SLOTS_CONFIG_PATH → 기본 경로.

    파일이 있으면: JSON 을 읽어 ModelTier 5개 이름 키만 추려 TierSlotRegistry 로
    구성한다 (상위 "_README" 등 문서용 주석 키는 무시). 5칸 중 누락/구조 불량이면
    IncompleteSlotRegistryError.

    파일이 없으면 (설계 4-5):
      - live 모드 → LiveModeFallbackError (NO-GO. placeholder 로 실호출 금지)
      - 그 외(mock 기본) → _mock_fallback_registry()
    """
    config_path = _resolve_config_path(path)

    if not config_path.is_file():
        mode = (llm_mode or os.getenv(LLM_MODE_ENV, "mock")).lower()
        if mode == "live":
            raise LiveModeFallbackError(
                f"CORTEX_LLM_MODE=live but no tier slots config at "
                f"'{config_path}'. Refusing to fall back to the mock template "
                f"for live calls (NO-GO). Provide {TIER_SLOTS_CONFIG_ENV} or "
                f"create the config file."
            )
        return _mock_fallback_registry()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IncompleteSlotRegistryError(
            f"Failed to read tier slots config '{config_path}': {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise IncompleteSlotRegistryError(
            f"Tier slots config '{config_path}' must be a JSON object, "
            f"got {type(raw).__name__}."
        )

    # ModelTier 5개 이름 키만 통과 — 주석/문서용 키(_README 등)는 자연 무시.
    valid_keys = {tier.name for tier in ModelTier}
    filtered = {k: v for k, v in raw.items() if k in valid_keys}

    try:
        return TierSlotRegistry(**filtered)
    except ValidationError as exc:
        present = sorted(filtered.keys())
        missing = sorted(valid_keys - set(filtered.keys()))
        raise IncompleteSlotRegistryError(
            f"Invalid tier slots config '{config_path}'. "
            f"All 5 tiers must be present and valid. "
            f"present={present} missing={missing}. Detail: {exc}"
        ) from exc


# ── 슬롯 / 키 조회 ───────────────────────────────────────────────────────────
def get_slot(
    tier: ModelTier,
    *,
    registry: TierSlotRegistry | None = None,
) -> TierSlot:
    """tier 에 해당하는 슬롯을 반환한다. **키를 요구하지 않는다** (위험 3 / 4-6).

    registry 미주입 시 load_tier_slots() 로 로드한다. (hot-path 캐싱은 V2 배선 시
    도입; V1 은 신규 모듈로 fresh load 또는 주입 registry 를 쓴다.)
    """
    reg = registry if registry is not None else load_tier_slots()
    return getattr(reg, tier.name)


def get_slot_api_key(
    tier: ModelTier,
    *,
    registry: TierSlotRegistry | None = None,
) -> str | None:
    """tier 슬롯의 API 키를 환경변수에서 동적 조회한다 (live 어댑터 전용).

    설계 4-2:
      - allow_empty_api_key=True  → 값(None/빈 문자열 포함)을 그대로 반환 (로컬/무인증)
      - allow_empty_api_key=False → api_key_env 가 있고 env 값이 비어있지 않아야 함.
                                     아니면 MissingApiKeyError.

    키 값은 로깅/예외 메시지에 절대 포함하지 않는다.
    """
    slot = get_slot(tier, registry=registry)
    value = os.getenv(slot.api_key_env) if slot.api_key_env else None

    if slot.allow_empty_api_key:
        return value

    if not value:
        raise MissingApiKeyError(
            f"Tier {tier.name} requires an API key but env "
            f"'{slot.api_key_env or '(unset)'}' is missing or empty "
            f"(allow_empty_api_key=False)."
        )
    return value


# ── 라이브 preflight (설계 4-4) — slot_registry 가 검증의 단일 소스 ───────────
# scripts/check_llm_slots.py 와 core /health 의 slots_ready 가 이 평가 로직을
# 공유한다. 검증이 두 곳으로 갈라지지 않도록 평가 함수는 여기 하나뿐이다.
_READY_STATUSES: frozenset[str] = frozenset({"OK", "OK_NO_AUTH"})


def evaluate_slot(slot: TierSlot) -> tuple[str, str]:
    """슬롯 1칸을 평가해 (status, detail) 반환. status 가 OK/OK_NO_AUTH 면 통과.

    네트워크 호출 없음. API 키 **값**은 절대 반환/노출하지 않는다 (env 이름까지만).
    status: UNSUPPORTED | INCOMPLETE | OK_NO_AUTH | MISSING_KEY | OK.
    """
    if slot.protocol not in KNOWN_PROTOCOLS:
        return "UNSUPPORTED", f"protocol={slot.protocol!r} (known: {sorted(KNOWN_PROTOCOLS)})"

    missing_fields = [
        name for name in ("base_url", "model") if not getattr(slot, name).strip()
    ]
    if missing_fields:
        return "INCOMPLETE", f"empty={','.join(missing_fields)} protocol={slot.protocol}"

    if slot.allow_empty_api_key:
        return "OK_NO_AUTH", f"protocol={slot.protocol} model={slot.model}"

    if not slot.api_key_env:
        # allow_empty_api_key=False 인데 env 이름조차 없음.
        return "MISSING_KEY", "env=(unset)"

    # 키 값은 읽되 출력하지 않는다 — presence 여부만 본다.
    if not os.getenv(slot.api_key_env):
        return "MISSING_KEY", f"env={slot.api_key_env}"

    return "OK", f"protocol={slot.protocol} model={slot.model}"


def slots_ready(
    *,
    path: str | None = None,
    llm_mode: str | None = None,
    registry: TierSlotRegistry | None = None,
) -> bool:
    """5칸이 모두 라이브 준비됐는지의 단일 집계 불리언 (strict AND).

    벤더 중립: 각 슬롯 자체의 api_key_env 만 보고 키 이름을 하드코딩하지 않는다.
    혼합 벤더 구성(칸마다 다른 키/protocol)에서도 동일하게 동작한다.

    True 조건: registry 로드 성공 + ModelTier 5칸 **전부** evaluate_slot 이
    OK/OK_NO_AUTH. 하나라도 아니면 False (strict AND, 첫 실패에서 단락).

    거짓 ready 금지: 설정 부재/불량(SlotRegistryError·LiveModeFallbackError)은
    예외를 흘리지 않고 False 로 흡수한다. 키 값/env 이름/벤더명을 노출하지 않으며
    SpinalLogger/인지 파이프라인 로그를 남기지 않는다(순수 파일·env 조회).
    """
    try:
        reg = registry if registry is not None else load_tier_slots(path, llm_mode=llm_mode)
    except SlotRegistryError:
        return False
    return all(
        evaluate_slot(getattr(reg, tier.name))[0] in _READY_STATUSES
        for tier in ModelTier
    )
