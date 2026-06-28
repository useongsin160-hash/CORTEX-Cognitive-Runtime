"""slot_registry.evaluate_slot / slots_ready preflight 재사용 테스트.

벤더 중립: 키 이름을 하드코딩하지 않고 슬롯 자체 api_key_env 만 본다. 키 **값**은
어디에도 노출하지 않는다(env 이름까지만). strict AND: 5칸이 *모두* OK/OK_NO_AUTH
일 때만 slots_ready=True. 거짓 ready 금지: 설정 부재/불량은 예외 없이 False.

네트워크 0, 무거운 임베더/chromadb 미로드.
"""
from __future__ import annotations

import json

import pytest

from app.core.model_tier import ModelTier
from app.core.slot_registry import evaluate_slot, load_tier_slots, slots_ready

_KEY_ENVS = [f"CORTEX_SLOT_{t.name}_KEY" for t in ModelTier]
_GEMINI = "CORTEX_GEMINI_API_KEY"
_ANTHROPIC = "ANTHROPIC_API_KEY"
_HEAVY_ENV = "CORTEX_SLOT_HEAVY_KEY"
_ARBITRARY = "TOTALLY_ARBITRARY_KEY_NAME"
_SECRET = "sk-SHOULD-NOT-LEAK-xyz"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TIER_SLOTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    for e in [*_KEY_ENVS, _GEMINI, _ANTHROPIC, _HEAVY_ENV, _ARBITRARY]:
        monkeypatch.delenv(e, raising=False)


def _write(tmp_path, slots: dict) -> str:
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(slots), encoding="utf-8")
    return str(p)


def _mixed_vendor_slots() -> dict:
    """칸마다 다른 api_key_env + protocol (벤더 혼합). 키 이름 하드코딩 불가 입증."""
    specs = {
        "LIGHTWEIGHT": (_GEMINI, "google", "gemini-x"),
        "MEDIUM": (_ANTHROPIC, "anthropic", "claude-x"),
        "STANDARD": (_GEMINI, "google", "gemini-y"),
        "HEAVY": (_HEAVY_ENV, "openai_compatible", "gpt-x"),
        "DEEP_THINKING": (_ANTHROPIC, "anthropic", "claude-y"),
    }
    return {
        name: {
            "base_url": "https://api.example.invalid",
            "api_key_env": env,
            "protocol": proto,
            "model": model,
            "allow_empty_api_key": False,
        }
        for name, (env, proto, model) in specs.items()
    }


# ── evaluate_slot 단칸 ──────────────────────────────────────────────────────
def test_evaluate_ok_when_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv(_GEMINI, _SECRET)
    reg = load_tier_slots(_write(tmp_path, _mixed_vendor_slots()))
    status, detail = evaluate_slot(reg.LIGHTWEIGHT)
    assert status == "OK"
    assert _SECRET not in detail  # 키 값은 detail 에 없다


def test_evaluate_missing_key_reports_env_name_not_value(tmp_path):
    reg = load_tier_slots(_write(tmp_path, _mixed_vendor_slots()))
    status, detail = evaluate_slot(reg.MEDIUM)  # ANTHROPIC_API_KEY 미설정
    assert status == "MISSING_KEY"
    assert detail == f"env={_ANTHROPIC}"  # env 이름만, 값 없음


def test_evaluate_ok_no_auth_when_allow_empty(tmp_path):
    slots = _mixed_vendor_slots()
    slots["HEAVY"]["allow_empty_api_key"] = True
    reg = load_tier_slots(_write(tmp_path, slots))
    status, _ = evaluate_slot(reg.HEAVY)
    assert status == "OK_NO_AUTH"


# ── slots_ready 집계 (strict AND) ───────────────────────────────────────────
def test_slots_ready_true_only_when_all_keys_present(tmp_path, monkeypatch):
    monkeypatch.setenv(_GEMINI, _SECRET)
    monkeypatch.setenv(_ANTHROPIC, _SECRET)
    monkeypatch.setenv(_HEAVY_ENV, _SECRET)
    reg = load_tier_slots(_write(tmp_path, _mixed_vendor_slots()))
    assert slots_ready(registry=reg) is True


def test_slots_ready_false_if_any_slot_missing_key(tmp_path, monkeypatch):
    # ANTHROPIC 만 누락 → MEDIUM/DEEP_THINKING MISSING_KEY → strict AND False.
    monkeypatch.setenv(_GEMINI, _SECRET)
    monkeypatch.setenv(_HEAVY_ENV, _SECRET)
    reg = load_tier_slots(_write(tmp_path, _mixed_vendor_slots()))
    assert slots_ready(registry=reg) is False


def test_slots_ready_arbitrary_key_name_no_hardcoding(tmp_path, monkeypatch):
    # 키 이름을 하드코딩하지 않으므로 임의 env 이름으로도 동작한다.
    monkeypatch.setenv(_ARBITRARY, _SECRET)
    slots = {
        t.name: {
            "base_url": "https://api.example.invalid",
            "api_key_env": _ARBITRARY,
            "protocol": "openai_compatible",
            "model": f"m-{t.name.lower()}",
            "allow_empty_api_key": False,
        }
        for t in ModelTier
    }
    assert slots_ready(registry=load_tier_slots(_write(tmp_path, slots))) is True


def test_slots_ready_false_on_incomplete_config():
    # 리포의 빈 양식: base_url/model 미기입 → INCOMPLETE → False (키 유무 무관).
    assert slots_ready(path="config/tier_slots.example.json") is False


def test_slots_ready_false_on_load_error(tmp_path):
    # live 모드 + 설정 부재 → LiveModeFallbackError 흡수 → False (거짓 ready 금지).
    missing = str(tmp_path / "does_not_exist.json")
    assert slots_ready(path=missing, llm_mode="live") is False
