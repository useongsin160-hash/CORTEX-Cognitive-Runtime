"""ModelTier + resolve_model (V2: Tier Slot Registry 위임).

V2 에서 MODEL_REGISTRY(3사 하드코딩 dict)는 폐기됐고, resolve_model 은 tier 슬롯의
model 을 반환한다. vendor 인자는 legacy 로 무시된다 (설계 4-3 / §5-1).
설정은 tmp 파일 + TIER_SLOTS_CONFIG_PATH env 로 격리한다.
"""
import json

import pytest

from app.core.model_tier import ModelTier, resolve_model


@pytest.fixture(autouse=True)
def _clean_slot_env(monkeypatch):
    monkeypatch.delenv("TIER_SLOTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)


def _write_config(tmp_path, **model_per_tier) -> str:
    """5칸 완전한 tier_slots.json 작성. model_per_tier 로 칸별 model override."""
    data = {}
    for tier in ModelTier:
        data[tier.name] = {
            "base_url": "https://api.example.com",
            "api_key_env": f"CORTEX_SLOT_{tier.name}_KEY",
            "protocol": "openai_compatible",
            "model": model_per_tier.get(tier.name, f"cfg-{tier.name.lower()}"),
        }
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ── ModelTier 불변 (회귀 가드 — 절대 변경 금지) ─────────────────────────────
def test_model_tier_has_exactly_five_levels():
    tiers = list(ModelTier)
    assert len(tiers) == 5
    assert [t.value for t in tiers] == [1, 2, 3, 4, 5]
    assert tiers == [
        ModelTier.LIGHTWEIGHT,
        ModelTier.MEDIUM,
        ModelTier.STANDARD,
        ModelTier.HEAVY,
        ModelTier.DEEP_THINKING,
    ]


# ── resolve_model 은 설정 슬롯의 model 을 반환 ──────────────────────────────
def test_resolve_model_returns_slot_model(tmp_path, monkeypatch):
    path = _write_config(tmp_path, STANDARD="cfg-standard")
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", path)
    assert resolve_model("legacy", ModelTier.STANDARD) == "cfg-standard"


def test_resolve_model_each_tier_from_slot(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", path)
    for tier in ModelTier:
        resolved = resolve_model("ignored", tier)
        assert resolved == f"cfg-{tier.name.lower()}"
        assert isinstance(resolved, str) and resolved


# ── vendor 인자는 legacy — 무시된다 ────────────────────────────────────────
def test_resolve_model_ignores_vendor(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", path)
    for tier in ModelTier:
        assert resolve_model("anthropic", tier) == resolve_model("anything-else", tier)
        assert resolve_model("", tier) == resolve_model("google", tier)


# ── 설정 부재 + mock 모드 → fallback model (키 무요구) ─────────────────────
def test_resolve_model_fallback_when_no_config(tmp_path, monkeypatch):
    missing = str(tmp_path / "does_not_exist.json")
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", missing)
    # CORTEX_LLM_MODE 미설정 → 기본 mock → fallback registry
    assert resolve_model("legacy", ModelTier.STANDARD) == "mock-standard"
    assert resolve_model("legacy", ModelTier.DEEP_THINKING) == "mock-deep_thinking"
