"""LiveLLMClient — V4 에서 스텁이 슬롯 기반 구현으로 대체됐다.

기존 'NotImplementedError 를 던진다' 단언은 더 이상 유효하지 않다. 여기서는
LiveLLMClient 가 인스턴스화되고 미지원 protocol 에 명확한 에러를 내는지만 가볍게
확인한다. 슬롯→어댑터→tier_used 확정 등 깊은 동작은
tests/execution/test_live_llm_client.py 가 담당한다.
"""
from __future__ import annotations

import json

import pytest

from app.core.model_tier import ModelTier
from app.execution.live_llm_client import LiveLLMClient, UnsupportedProtocolError
from app.execution.params import GenerationParams


def _write_config(tmp_path, protocol: str) -> str:
    data = {
        tier.name: {
            "base_url": "https://api.example.com",
            "api_key_env": None,
            "protocol": protocol,
            "model": f"m-{tier.name.lower()}",
            "allow_empty_api_key": True,
        }
        for tier in ModelTier
    }
    p = tmp_path / "tier_slots.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_live_client_instantiates():
    assert isinstance(LiveLLMClient(), LiveLLMClient)


@pytest.mark.asyncio
async def test_unsupported_protocol_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TIER_SLOTS_CONFIG_PATH", _write_config(tmp_path, "no_such_protocol"))
    client = LiveLLMClient()
    with pytest.raises(UnsupportedProtocolError):
        await client.generate("q", ModelTier.STANDARD, GenerationParams())
