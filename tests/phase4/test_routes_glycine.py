"""Phase 4 STEP 5.1 — routes.py Glycine pre-flight integration tests.

Uses TestClient; Glycine is replaced on app.state so we don't need
to synthesize 16 000-char prompts in every test.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routing.neuromodulators import Glycine, GlycineConfig, GlycineDecision


@pytest.fixture(autouse=True)
def _restore_glycine():
    """Restore app.state.glycine to default after every test in this module."""
    original = app.state.glycine
    yield
    app.state.glycine = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AlwaysBlockGlycine(Glycine):
    async def check_pre_flight(self, prompt: str, session_key: str) -> GlycineDecision:
        return GlycineDecision(
            active=True, reason="test_block", action="block",
        )


class _NeverBlockGlycine(Glycine):
    async def check_pre_flight(self, prompt: str, session_key: str) -> GlycineDecision:
        return GlycineDecision(active=False)


def _client_with_glycine(glycine: Glycine) -> TestClient:
    app.state.glycine = glycine
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Glycine blocks → fallback response
# ---------------------------------------------------------------------------

def test_glycine_block_returns_fallback_response_source():
    client = _client_with_glycine(_AlwaysBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["response_source"] == "fallback"


def test_glycine_block_sets_glycine_active_true():
    client = _client_with_glycine(_AlwaysBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello"})
    data = resp.json()
    assert data["glycine_active"] is True


def test_glycine_block_populates_reason_and_action():
    client = _client_with_glycine(_AlwaysBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello"})
    data = resp.json()
    assert data["glycine_reason"] == "test_block"
    assert data["glycine_action"] == "block"


def test_glycine_block_answer_contains_blocked_marker():
    client = _client_with_glycine(_AlwaysBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello"})
    data = resp.json()
    assert "[GLYCINE BLOCKED]" in data["answer"]


def test_glycine_block_path_taken_is_glycine_blocked():
    client = _client_with_glycine(_AlwaysBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello"})
    data = resp.json()
    assert data["path_taken"] == "glycine_blocked"


# ---------------------------------------------------------------------------
# Glycine passes → downstream pipeline runs, glycine_active=False
# ---------------------------------------------------------------------------

def test_glycine_pass_sets_glycine_active_false():
    client = _client_with_glycine(_NeverBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello world"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["glycine_active"] is False


def test_glycine_pass_glycine_reason_is_none():
    client = _client_with_glycine(_NeverBlockGlycine())
    resp = client.post("/query", json={"prompt": "hello world"})
    data = resp.json()
    assert data["glycine_reason"] is None
    assert data["glycine_action"] is None


# ---------------------------------------------------------------------------
# Real Glycine: token budget triggers on a long prompt
# ---------------------------------------------------------------------------

def test_real_glycine_token_budget_blocks_long_prompt():
    # token_budget=10 → 40 chars triggers the block
    app.state.glycine = Glycine(GlycineConfig(token_budget=10))
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/query", json={"prompt": "a" * 50})  # 50 // 4 = 12 >= 10
    data = resp.json()
    assert data["glycine_active"] is True
    assert "token_budget_exceeded" in data["glycine_reason"]


def test_real_glycine_short_prompt_passes():
    app.state.glycine = Glycine(GlycineConfig(token_budget=10))
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post("/query", json={"prompt": "hi"})
    data = resp.json()
    assert data["glycine_active"] is False
