"""OVERTURE A3 — 슬롯 차등 실재화 (config 레벨, 네트워크 0).

두 가지를 입증한다:
  1. slots_ready 가 멀티벤더(3종 protocol·슬롯별 distinct api_key_env) 구성에서 키
     이름 하드코딩 없이 동작하고, 미지원 protocol 슬롯을 False 로 거른다.
  2. A1 의 cache_key.slot_fingerprint 가 슬롯 차등(protocol/base_url/model)에 민감해
     슬롯마다 다른 fingerprint 를 낸다 — 캐시 seam 의 슬롯 네임스페이스가 실재함을
     확인(캐시 로직 자체는 변경하지 않는다). API key 값/env 이름은 fingerprint
     시그니처에 구조적으로 부재.
갱신된 example 템플릿이 멀티벤더이면서 base_url 공백으로 INCOMPLETE→NO-GO 임도 확인.
"""
from __future__ import annotations

import inspect
import json

import pytest

from app.core.model_tier import ModelTier
from app.core.slot_registry import evaluate_slot, load_tier_slots, slots_ready
from app.ingress.cache_key import slot_fingerprint

_KEY_ENVS = [f"CORTEX_SLOT_{t.name}_KEY" for t in ModelTier]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TIER_SLOTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    for e in _KEY_ENVS:
        monkeypatch.delenv(e, raising=False)


_SPECS = {
    "LIGHTWEIGHT": ("openai_compatible", "m-light"),
    "MEDIUM": ("google", "m-mid"),
    "STANDARD": ("anthropic", "m-standard"),
    "HEAVY": ("openai_compatible", "m-heavy"),
    "DEEP_THINKING": ("anthropic", "m-deep"),
}


def _write_multivendor(tmp_path, *, override: dict | None = None) -> str:
    override = override or {}
    data = {}
    for tier in ModelTier:
        proto, model = _SPECS[tier.name]
        ov = override.get(tier.name, {})
        data[tier.name] = {
            "base_url": f"https://{tier.name.lower()}.api.invalid",
            "api_key_env": f"CORTEX_SLOT_{tier.name}_KEY",
            "protocol": ov.get("protocol", proto),
            "model": ov.get("model", model),
            "allow_empty_api_key": False,
        }
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ── slots_ready over 멀티벤더 ───────────────────────────────────────────────
def test_slots_ready_multivendor_all_keys_present(tmp_path, monkeypatch):
    reg = load_tier_slots(_write_multivendor(tmp_path))
    for tier in ModelTier:
        monkeypatch.setenv(getattr(reg, tier.name).api_key_env, "k")
    assert slots_ready(registry=reg) is True  # 키 이름 하드코딩 없이 슬롯 자체 env


def test_slots_ready_false_when_one_vendor_key_missing(tmp_path, monkeypatch):
    reg = load_tier_slots(_write_multivendor(tmp_path))
    envs = [getattr(reg, t.name).api_key_env for t in ModelTier]
    for e in envs[1:]:
        monkeypatch.setenv(e, "k")
    monkeypatch.delenv(envs[0], raising=False)  # 한 슬롯만 키 누락
    assert slots_ready(registry=reg) is False  # strict AND


def test_slots_ready_false_on_unsupported_protocol(tmp_path, monkeypatch):
    reg = load_tier_slots(
        _write_multivendor(tmp_path, override={"DEEP_THINKING": {"protocol": "llama_v3_custom"}})
    )
    for tier in ModelTier:
        monkeypatch.setenv(getattr(reg, tier.name).api_key_env, "k")
    # 키가 전부 있어도 미지원 protocol 슬롯이 UNSUPPORTED → 거름.
    assert slots_ready(registry=reg) is False
    assert evaluate_slot(reg.DEEP_THINKING)[0] == "UNSUPPORTED"


# ── slot_fingerprint 차등 민감도 (A1 seam) ──────────────────────────────────
def test_fingerprint_signature_excludes_secrets():
    params = set(inspect.signature(slot_fingerprint).parameters)
    assert params == {"tier_name", "protocol", "base_url", "model"}
    assert not any(("key" in p or "secret" in p or "token" in p) for p in params)


def test_fingerprint_is_deterministic_and_prefixed():
    args = dict(tier_name="HEAVY", protocol="openai_compatible",
                base_url="https://x.invalid", model="m")
    assert slot_fingerprint(**args) == slot_fingerprint(**args)
    assert slot_fingerprint(**args).startswith("sfp_")


@pytest.mark.parametrize("field,a,b", [
    ("model", "model-a", "model-b"),
    ("protocol", "anthropic", "google"),
    ("base_url", "https://a.invalid", "https://b.invalid"),
])
def test_fingerprint_sensitive_to_each_field(field, a, b):
    base = dict(tier_name="STANDARD", protocol="anthropic",
                base_url="https://x.invalid", model="m")
    assert slot_fingerprint(**{**base, field: a}) != slot_fingerprint(**{**base, field: b})


def test_differentiated_slots_yield_distinct_fingerprints(tmp_path):
    reg = load_tier_slots(_write_multivendor(tmp_path))
    fps = {
        slot_fingerprint(
            tier_name=tier.name,
            protocol=getattr(reg, tier.name).protocol,
            base_url=getattr(reg, tier.name).base_url,
            model=getattr(reg, tier.name).model,
        )
        for tier in ModelTier
    }
    assert len(fps) == 5  # 5칸 전부 다른 슬롯 네임스페이스


# ── 갱신된 example 템플릿: 멀티벤더 + INCOMPLETE/NO-GO ──────────────────────
def test_example_template_is_multivendor_and_no_go():
    reg = load_tier_slots(path="config/tier_slots.example.json")
    protocols = {getattr(reg, t.name).protocol for t in ModelTier}
    assert len(protocols) >= 3                                   # 3종 이상 혼합
    assert protocols <= {"openai_compatible", "anthropic", "google"}
    assert len({getattr(reg, t.name).api_key_env for t in ModelTier}) == 5  # 슬롯별 distinct env
    assert len({getattr(reg, t.name).model for t in ModelTier}) == 5        # tier별 model 차등
    # base_url 공백 → INCOMPLETE → NO-GO (정직한 미완성 신호; 키만 꽂으면 됨처럼 보고하지 않음)
    assert evaluate_slot(reg.LIGHTWEIGHT)[0] == "INCOMPLETE"
    assert slots_ready(path="config/tier_slots.example.json") is False
