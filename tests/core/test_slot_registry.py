"""Tier Slot Registry V1 단위 테스트 (docs/TIER_SLOT_REGISTRY_DESIGN.md §8).

네트워크 호출 없음. env 는 monkeypatch, 설정 파일은 tmp_path 로 격리한다.
load_tier_slots(..., llm_mode=...) 로 모드를 명시 주입해 CORTEX_LLM_MODE 환경에
의존하지 않게 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.model_tier import ModelTier
from app.core.slot_registry import (
    KNOWN_PROTOCOLS,
    IncompleteSlotRegistryError,
    LiveModeFallbackError,
    MissingApiKeyError,
    TierSlot,
    TierSlotRegistry,
    get_slot,
    get_slot_api_key,
    load_tier_slots,
)

_ALL_KEY_ENVS = [
    "CORTEX_SLOT_LIGHTWEIGHT_KEY",
    "CORTEX_SLOT_MEDIUM_KEY",
    "CORTEX_SLOT_STANDARD_KEY",
    "CORTEX_SLOT_HEAVY_KEY",
    "CORTEX_SLOT_DEEP_KEY",
    "TIER_SLOTS_CONFIG_PATH",
    "CORTEX_LLM_MODE",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """슬롯 관련 환경변수를 전부 제거해 테스트 간 누수를 막는다."""
    for name in _ALL_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


def _slot(**overrides) -> dict:
    base = {
        "base_url": "https://api.example.com",
        "api_key_env": "CORTEX_SLOT_X_KEY",
        "protocol": "openai_compatible",
        "model": "example-model",
    }
    base.update(overrides)
    return base


def _full_dict(**per_tier) -> dict:
    """5칸 완전한 설정 dict. per_tier 로 칸별 override."""
    data = {}
    for tier in ModelTier:
        data[tier.name] = _slot(**per_tier.get(tier.name, {}))
    return data


def _write(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ── 1. 완전한 5칸 로드 ───────────────────────────────────────────────────────
def test_full_five_slots_loads(tmp_path):
    path = _write(tmp_path, _full_dict())
    reg = load_tier_slots(path)
    assert isinstance(reg, TierSlotRegistry)
    for tier in ModelTier:
        assert isinstance(getattr(reg, tier.name), TierSlot)


# ── 2. 5칸 미충족 → IncompleteSlotRegistryError ─────────────────────────────
def test_missing_slot_raises_incomplete(tmp_path):
    data = _full_dict()
    del data["HEAVY"]
    path = _write(tmp_path, data)
    with pytest.raises(IncompleteSlotRegistryError) as exc:
        load_tier_slots(path)
    assert "HEAVY" in str(exc.value)  # 누락 칸 안내


def test_non_object_json_raises_incomplete(tmp_path):
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(IncompleteSlotRegistryError):
        load_tier_slots(str(p))


# ── 3. get_slot 은 키 없이 동작 (위험 3 / 4-6) ───────────────────────────────
def test_get_slot_works_without_any_key(tmp_path):
    # api_key_env 가 지정돼 있으나 어떤 env 도 설정돼 있지 않은 상태.
    path = _write(tmp_path, _full_dict())
    reg = load_tier_slots(path)
    slot = get_slot(ModelTier.STANDARD, registry=reg)
    assert slot.api_key_env == "CORTEX_SLOT_X_KEY"
    assert slot.base_url == "https://api.example.com"  # 키 조회 없이 반환됨


# ── 4. 각 slot 독립 protocol/model/key_env/base_url ─────────────────────────
def test_slots_are_independent(tmp_path):
    data = _full_dict(
        LIGHTWEIGHT={"protocol": "google", "model": "m-light",
                     "base_url": "https://light", "api_key_env": "K_LIGHT"},
        DEEP_THINKING={"protocol": "anthropic", "model": "m-deep",
                       "base_url": "https://deep", "api_key_env": "K_DEEP"},
    )
    reg = load_tier_slots(_write(tmp_path, data))
    light = get_slot(ModelTier.LIGHTWEIGHT, registry=reg)
    deep = get_slot(ModelTier.DEEP_THINKING, registry=reg)
    assert (light.protocol, light.model, light.base_url, light.api_key_env) == (
        "google", "m-light", "https://light", "K_LIGHT")
    assert (deep.protocol, deep.model, deep.base_url, deep.api_key_env) == (
        "anthropic", "m-deep", "https://deep", "K_DEEP")


# ── 5. 같은/다른 api_key_env 공유 허용 ───────────────────────────────────────
def test_shared_and_distinct_key_envs_allowed(tmp_path):
    shared = _full_dict(
        LIGHTWEIGHT={"api_key_env": "SHARED_KEY"},
        MEDIUM={"api_key_env": "SHARED_KEY"},
        STANDARD={"api_key_env": "DISTINCT_KEY"},
    )
    reg = load_tier_slots(_write(tmp_path, shared))
    assert get_slot(ModelTier.LIGHTWEIGHT, registry=reg).api_key_env == "SHARED_KEY"
    assert get_slot(ModelTier.MEDIUM, registry=reg).api_key_env == "SHARED_KEY"
    assert get_slot(ModelTier.STANDARD, registry=reg).api_key_env == "DISTINCT_KEY"


# ── 6. allow_empty_api_key=True → 키 없이 허용 ──────────────────────────────
def test_allow_empty_api_key_returns_without_error(tmp_path):
    data = _full_dict(
        LIGHTWEIGHT={"allow_empty_api_key": True, "api_key_env": None},
    )
    reg = load_tier_slots(_write(tmp_path, data))
    # env 미설정이어도 예외 없이 None 반환.
    assert get_slot_api_key(ModelTier.LIGHTWEIGHT, registry=reg) is None


def test_allow_empty_api_key_true_returns_value_when_present(tmp_path, monkeypatch):
    data = _full_dict(
        LIGHTWEIGHT={"allow_empty_api_key": True, "api_key_env": "LOCAL_KEY"},
    )
    reg = load_tier_slots(_write(tmp_path, data))
    monkeypatch.setenv("LOCAL_KEY", "local-secret")
    assert get_slot_api_key(ModelTier.LIGHTWEIGHT, registry=reg) == "local-secret"


# ── 7. allow_empty_api_key=False + 키 없음 → MissingApiKeyError (값 미노출) ──
def test_missing_key_raises_and_message_has_no_value(tmp_path):
    data = _full_dict(STANDARD={"allow_empty_api_key": False,
                                "api_key_env": "CORTEX_SLOT_STANDARD_KEY"})
    reg = load_tier_slots(_write(tmp_path, data))
    with pytest.raises(MissingApiKeyError) as exc:
        get_slot_api_key(ModelTier.STANDARD, registry=reg)
    msg = str(exc.value)
    assert "STANDARD" in msg
    assert "CORTEX_SLOT_STANDARD_KEY" in msg  # env 이름은 허용
    # 값은 존재하지 않지만, 우연한 누수 방지: 흔한 secret 토큰 패턴이 없어야 함.
    assert "sk-" not in msg


def test_missing_key_env_unset_name_raises(tmp_path):
    # allow_empty=False 인데 api_key_env 자체가 None.
    data = _full_dict(HEAVY={"allow_empty_api_key": False, "api_key_env": None})
    reg = load_tier_slots(_write(tmp_path, data))
    with pytest.raises(MissingApiKeyError):
        get_slot_api_key(ModelTier.HEAVY, registry=reg)


# ── 8. allow_empty_api_key=False + 키 있음 → 값 반환 ────────────────────────
def test_present_key_returns_value(tmp_path, monkeypatch):
    data = _full_dict(DEEP_THINKING={"allow_empty_api_key": False,
                                     "api_key_env": "CORTEX_SLOT_DEEP_KEY"})
    reg = load_tier_slots(_write(tmp_path, data))
    monkeypatch.setenv("CORTEX_SLOT_DEEP_KEY", "deep-secret-value")
    assert get_slot_api_key(ModelTier.DEEP_THINKING, registry=reg) == "deep-secret-value"


def test_present_but_empty_key_raises(tmp_path, monkeypatch):
    data = _full_dict(MEDIUM={"allow_empty_api_key": False,
                              "api_key_env": "CORTEX_SLOT_MEDIUM_KEY"})
    reg = load_tier_slots(_write(tmp_path, data))
    monkeypatch.setenv("CORTEX_SLOT_MEDIUM_KEY", "")  # 빈 값 = 없음 취급
    with pytest.raises(MissingApiKeyError):
        get_slot_api_key(ModelTier.MEDIUM, registry=reg)


# ── 9. 파일 없음 + mock → fallback (키 없이 동작) ───────────────────────────
def test_missing_file_mock_returns_fallback(tmp_path):
    missing = str(tmp_path / "does_not_exist.json")
    reg = load_tier_slots(missing, llm_mode="mock")
    assert isinstance(reg, TierSlotRegistry)
    for tier in ModelTier:
        slot = get_slot(tier, registry=reg)
        assert slot.allow_empty_api_key is True
        # fallback 은 키 없이 동작해야 한다.
        assert get_slot_api_key(tier, registry=reg) is None


def test_missing_file_default_mode_is_mock(tmp_path, monkeypatch):
    # CORTEX_LLM_MODE 미설정(기본 mock) → fallback.
    missing = str(tmp_path / "nope.json")
    reg = load_tier_slots(missing)  # llm_mode 생략 → env 없음 → mock
    assert isinstance(reg, TierSlotRegistry)


# ── 10. 파일 없음 + live → NO-GO ────────────────────────────────────────────
def test_missing_file_live_raises_nogo(tmp_path):
    missing = str(tmp_path / "does_not_exist.json")
    with pytest.raises(LiveModeFallbackError):
        load_tier_slots(missing, llm_mode="live")


def test_missing_file_live_via_env_raises_nogo(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_LLM_MODE", "live")
    missing = str(tmp_path / "does_not_exist.json")
    with pytest.raises(LiveModeFallbackError):
        load_tier_slots(missing)


# ── 11. _README 등 주석 키 무시 ─────────────────────────────────────────────
def test_readme_and_comment_keys_ignored(tmp_path):
    data = _full_dict()
    data["_README"] = ["this is documentation", "ignored by loader"]
    data["_note"] = "also ignored"
    reg = load_tier_slots(_write(tmp_path, data))
    assert isinstance(reg, TierSlotRegistry)
    assert get_slot(ModelTier.LIGHTWEIGHT, registry=reg).protocol == "openai_compatible"


# ── 보조: KNOWN_PROTOCOLS ───────────────────────────────────────────────────
def test_known_protocols_contains_three_defaults():
    assert {"openai_compatible", "anthropic", "google"} <= KNOWN_PROTOCOLS


# ── preflight 평가 로직 (slot_registry 가 단일 소스; check_llm_slots 가 위임) ─
def test_check_script_evaluate_statuses(monkeypatch):
    from app.core.slot_registry import evaluate_slot as _evaluate

    ok_no_auth = TierSlot(base_url="http://local", api_key_env=None,
                          protocol="openai_compatible", model="m",
                          allow_empty_api_key=True)
    assert _evaluate(ok_no_auth)[0] == "OK_NO_AUTH"

    unsupported = TierSlot(base_url="http://x", protocol="custom_http", model="m")
    assert _evaluate(unsupported)[0] == "UNSUPPORTED"

    incomplete = TierSlot(base_url="", protocol="anthropic", model="")
    assert _evaluate(incomplete)[0] == "INCOMPLETE"

    missing_key = TierSlot(base_url="http://x", api_key_env="SOME_UNSET_ENV",
                           protocol="google", model="m")
    monkeypatch.delenv("SOME_UNSET_ENV", raising=False)
    status, detail = _evaluate(missing_key)
    assert status == "MISSING_KEY"
    assert "SOME_UNSET_ENV" in detail

    monkeypatch.setenv("SOME_SET_ENV", "secret")
    ok = TierSlot(base_url="http://x", api_key_env="SOME_SET_ENV",
                  protocol="google", model="m")
    status, detail = _evaluate(ok)
    assert status == "OK"
    assert "secret" not in detail  # 키 값 미노출
