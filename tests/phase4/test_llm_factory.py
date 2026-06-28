"""Phase 4 STEP 1 — LLMClient factory."""
from __future__ import annotations

import pytest

from app.execution.factory import get_llm_client
from app.execution.live_llm_client import LiveLLMClient
from app.execution.mock_llm_client import MockLLMClient


def test_mock_mode_returns_mock_client(monkeypatch):
    monkeypatch.setenv("CORTEX_LLM_MODE", "mock")
    assert isinstance(get_llm_client(), MockLLMClient)


def test_live_mode_returns_live_client(monkeypatch):
    monkeypatch.setenv("CORTEX_LLM_MODE", "live")
    assert isinstance(get_llm_client(), LiveLLMClient)


def test_unknown_mode_raises_value_error(monkeypatch):
    monkeypatch.setenv("CORTEX_LLM_MODE", "banana")
    with pytest.raises(ValueError, match="Unknown CORTEX_LLM_MODE"):
        get_llm_client()


def test_unset_env_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("CORTEX_LLM_MODE", raising=False)
    assert isinstance(get_llm_client(), MockLLMClient)


def test_mode_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("CORTEX_LLM_MODE", "MOCK")
    assert isinstance(get_llm_client(), MockLLMClient)
