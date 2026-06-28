"""Phase 4 STEP 5.1 — Glycine unit tests.

Covers: token_budget, rate_limit, loop_guard guards and record-on-pass semantics.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.routing.neuromodulators import Glycine, GlycineConfig, GlycineDecision


# ---------------------------------------------------------------------------
# Token budget guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_budget_passes_short_prompt():
    g = Glycine(GlycineConfig(token_budget=100))
    d = await g.check_pre_flight("hello", "s1")
    assert d.active is False


@pytest.mark.asyncio
async def test_token_budget_blocks_when_exceeded():
    # 400 chars // 4 = 100 tokens — exactly at budget → blocked
    g = Glycine(GlycineConfig(token_budget=100))
    long_prompt = "x" * 400
    d = await g.check_pre_flight(long_prompt, "s1")
    assert d.active is True
    assert "token_budget_exceeded" in (d.reason or "")
    assert d.action == "block"


@pytest.mark.asyncio
async def test_token_budget_blocks_when_over():
    g = Glycine(GlycineConfig(token_budget=10))
    d = await g.check_pre_flight("a" * 50, "s1")  # 50 // 4 = 12 >= 10
    assert d.active is True


@pytest.mark.asyncio
async def test_token_budget_no_record_on_block():
    g = Glycine(GlycineConfig(token_budget=1))
    await g.check_pre_flight("aaaa", "s1")  # 4 // 4 = 1 → blocked
    state = g._sessions.get("s1")
    assert state is None or len(state.request_timestamps) == 0


# ---------------------------------------------------------------------------
# Rate limit guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_allows_up_to_max():
    g = Glycine(GlycineConfig(rate_max_requests=3, rate_window_seconds=60.0))
    for i in range(3):
        d = await g.check_pre_flight(f"prompt_{i}", "s1")
        assert d.active is False, f"request {i} should pass"


@pytest.mark.asyncio
async def test_rate_limit_blocks_at_limit():
    g = Glycine(GlycineConfig(rate_max_requests=3, rate_window_seconds=60.0))
    for i in range(3):
        await g.check_pre_flight(f"prompt_{i}", "s1")
    # 4th request — 3 timestamps recorded, 3 >= 3 → block
    d = await g.check_pre_flight("prompt_new", "s1")
    assert d.active is True
    assert "rate_limit_exceeded" in (d.reason or "")
    assert d.action == "block"


@pytest.mark.asyncio
async def test_rate_limit_expired_timestamps_not_counted():
    g = Glycine(GlycineConfig(rate_max_requests=2, rate_window_seconds=1.0))
    # Manually inject old timestamps
    state = g._session("s1")
    state.request_timestamps.extend([time.monotonic() - 10.0, time.monotonic() - 10.0])
    # Old timestamps outside 1s window — current count = 0 → should pass
    d = await g.check_pre_flight("fresh_prompt", "s1")
    assert d.active is False


@pytest.mark.asyncio
async def test_rate_limit_no_record_on_block():
    g = Glycine(GlycineConfig(rate_max_requests=1, rate_window_seconds=60.0))
    await g.check_pre_flight("first", "s1")  # passes, records
    state = g._session("s1")
    count_before = len(state.request_timestamps)
    await g.check_pre_flight("second", "s1")  # blocked
    assert len(state.request_timestamps) == count_before


@pytest.mark.asyncio
async def test_rate_limit_independent_sessions():
    g = Glycine(GlycineConfig(rate_max_requests=2, rate_window_seconds=60.0))
    await g.check_pre_flight("p1", "session_a")
    await g.check_pre_flight("p2", "session_a")
    # session_a is now at limit; session_b should be independent
    d = await g.check_pre_flight("p3", "session_b")
    assert d.active is False


# ---------------------------------------------------------------------------
# Loop guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_guard_passes_below_threshold():
    g = Glycine(GlycineConfig(loop_threshold=5, loop_window_seconds=60.0))
    prompt = "same prompt"
    for _ in range(4):
        d = await g.check_pre_flight(prompt, "s1")
        assert d.active is False


@pytest.mark.asyncio
async def test_loop_guard_blocks_at_threshold():
    g = Glycine(GlycineConfig(loop_threshold=5, loop_window_seconds=60.0))
    prompt = "repeated prompt"
    for _ in range(4):
        await g.check_pre_flight(prompt, "s1")
    # 5th attempt: same_count=4, 4+1=5 >= 5 → block
    d = await g.check_pre_flight(prompt, "s1")
    assert d.active is True
    assert "loop_detected" in (d.reason or "")
    assert d.action == "block"


@pytest.mark.asyncio
async def test_loop_guard_different_prompts_no_block():
    g = Glycine(GlycineConfig(loop_threshold=3, loop_window_seconds=60.0))
    for i in range(10):
        d = await g.check_pre_flight(f"unique prompt {i}", "s1")
        assert d.active is False


@pytest.mark.asyncio
async def test_loop_guard_expired_history_not_counted():
    g = Glycine(GlycineConfig(loop_threshold=2, loop_window_seconds=1.0))
    state = g._session("s1")
    import hashlib
    prompt = "old prompt"
    h = hashlib.md5(prompt.encode()).hexdigest()[:8]
    # Inject old history outside the 1s window
    state.prompt_history.append((h, time.monotonic() - 10.0))
    # Even though old entry exists, it's outside the window → count=0, 0+1=1 < 2 → pass
    d = await g.check_pre_flight(prompt, "s1")
    assert d.active is False


@pytest.mark.asyncio
async def test_loop_guard_no_record_on_block():
    g = Glycine(GlycineConfig(loop_threshold=2, loop_window_seconds=60.0))
    prompt = "blocked prompt"
    await g.check_pre_flight(prompt, "s1")  # passes, records
    state = g._session("s1")
    history_len_before = len(state.prompt_history)
    await g.check_pre_flight(prompt, "s1")  # blocked (2nd attempt = threshold)
    assert len(state.prompt_history) == history_len_before


# ---------------------------------------------------------------------------
# Guard ordering: token_budget runs before session state lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_budget_checked_before_rate_limit():
    # Even if the session has no history, an oversized prompt is blocked first.
    g = Glycine(GlycineConfig(token_budget=1, rate_max_requests=100))
    d = await g.check_pre_flight("xxxx", "s1")  # 4 // 4 = 1 >= 1 → token block
    assert d.active is True
    assert "token_budget_exceeded" in (d.reason or "")
