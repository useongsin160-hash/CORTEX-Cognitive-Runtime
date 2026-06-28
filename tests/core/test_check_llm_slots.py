"""scripts/check_llm_slots.py preflight 테스트 (네트워크 0, 키 값 미노출).

검증:
  - 빈 템플릿(example) → INCOMPLETE → NO-GO (base_url/model 미기입).
  - shared-key 구성(같은 api_key_env를 여러 칸이 공유) → 키 존재 시 GO.
  - missing key → MISSING_KEY로 보고하되 키 값은 절대 출력하지 않음(env 이름까지만).
"""
from __future__ import annotations

import json

import pytest

from app.core.model_tier import ModelTier
from scripts.check_llm_slots import main

_KEY_ENVS = [f"CORTEX_SLOT_{t.name}_KEY" for t in ModelTier]
_SHARED_ENV = "CORTEX_SHARED_SMOKE_KEY"
_SECRET_VALUE = "sk-SHOULD-NOT-BE-PRINTED-123"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TIER_SLOTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    monkeypatch.delenv(_SHARED_ENV, raising=False)
    for e in _KEY_ENVS:
        monkeypatch.delenv(e, raising=False)


def _write(tmp_path, slots: dict) -> str:
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(slots), encoding="utf-8")
    return str(p)


def test_example_template_is_no_go(capsys):
    # 리포의 빈 양식: base_url/model이 비어 INCOMPLETE → NO-GO.
    rc = main(["--path", "config/tier_slots.example.json"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "RESULT: NO-GO" in out
    assert "INCOMPLETE" in out


def test_shared_key_across_slots_is_go(tmp_path, monkeypatch, capsys):
    # 5칸이 같은 api_key_env(_SHARED_ENV)를 공유 — key/env 재사용 허용.
    monkeypatch.setenv(_SHARED_ENV, _SECRET_VALUE)
    slots = {
        tier.name: {
            "base_url": "https://api.example.invalid",
            "api_key_env": _SHARED_ENV,
            "protocol": "openai_compatible",
            "model": f"m-{tier.name.lower()}",
            "allow_empty_api_key": False,
        }
        for tier in ModelTier
    }
    rc = main(["--path", _write(tmp_path, slots)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RESULT: GO" in out
    # 키 값은 절대 출력되지 않는다.
    assert _SECRET_VALUE not in out


def test_missing_key_is_redacted(tmp_path, capsys):
    # api_key_env는 지정됐지만 env 미설정 → MISSING_KEY. 값 누출 없음(애초에 값 없음).
    slots = {
        tier.name: {
            "base_url": "https://api.example.invalid",
            "api_key_env": f"CORTEX_SLOT_{tier.name}_KEY",
            "protocol": "openai_compatible",
            "model": f"m-{tier.name.lower()}",
            "allow_empty_api_key": False,
        }
        for tier in ModelTier
    }
    rc = main(["--path", _write(tmp_path, slots)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISSING_KEY" in out
    # env 이름은 보고하되 (값이 아니라 이름) — 값 자체는 환경에 없음.
    assert "CORTEX_SLOT_LIGHTWEIGHT_KEY" in out
    assert _SECRET_VALUE not in out
