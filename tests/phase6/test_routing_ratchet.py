"""B11 S4 — monotonic routing ratchet (session no-demote).

Rise = learning (override/baseline raises the floor), fall = forgetting (S5 decay
only). Verifies: demote clamp, promote pass-through + floor rise, per-(category,
difficulty) independence, B12-native baseline protection of high difficulty,
session boundary reset, bounded LRU eviction, and the reserved S5 lower_floor hook.
"""
from __future__ import annotations

import pytest

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import SpinalLogger
from app.routing.routing_ratchet import RoutingRatchet
from app.routing.skip_router import RouteDecision


def _ratchet(max_sessions: int = 512) -> RoutingRatchet:
    return RoutingRatchet(logger=SpinalLogger(), max_sessions=max_sessions)


def _tc(category: str = "coding", difficulty: Difficulty = Difficulty.MEDIUM) -> TaskContext:
    return TaskContext(trace_id="t", category=category, difficulty=difficulty)


def _dec(path: str) -> RouteDecision:
    return RouteDecision(path=path, skip_layers=[], reason="b12")


# ── demote blocked / promote allowed ───────────────────────────────────────
@pytest.mark.asyncio
async def test_demote_blocked_after_floor_set():
    r = _ratchet()
    out1 = await r.apply(_dec("standard"), _tc(), "s")  # floor → standard
    assert out1.path == "standard"
    out2 = await r.apply(_dec("lightweight"), _tc(), "s")  # demote attempt
    assert out2.path == "standard"  # clamped up to floor
    assert "ratchet_floor" in out2.reason


@pytest.mark.asyncio
async def test_promote_allowed_and_raises_floor():
    r = _ratchet()
    await r.apply(_dec("standard"), _tc(), "s")  # floor standard
    out = await r.apply(_dec("full_pipeline"), _tc(), "s")  # promote
    assert out.path == "full_pipeline"
    # floor now full_pipeline → a later standard is clamped up.
    out2 = await r.apply(_dec("standard"), _tc(), "s")
    assert out2.path == "full_pipeline"


# ── per (category, difficulty) independence — no over-protection ───────────
@pytest.mark.asyncio
async def test_floor_independent_per_category_difficulty():
    r = _ratchet()
    # coding:HARD floored at full_pipeline...
    await r.apply(_dec("full_pipeline"), _tc("coding", Difficulty.HARD), "s")
    # ...does NOT force coding:EASY up — its own floor is independent.
    out = await r.apply(_dec("lightweight"), _tc("coding", Difficulty.EASY), "s")
    assert out.path == "lightweight"


# ── B12-native baseline protects high difficulty ───────────────────────────
@pytest.mark.asyncio
async def test_b12_native_full_pipeline_protected():
    r = _ratchet()
    # difficulty 4·5 first request is B12 full_pipeline → floor full_pipeline.
    out = await r.apply(_dec("full_pipeline"), _tc("coding", Difficulty.VERY_HARD), "s")
    assert out.path == "full_pipeline"
    # even if the cell later learns low and override wants a shortcut → blocked.
    out2 = await r.apply(_dec("lightweight"), _tc("coding", Difficulty.VERY_HARD), "s")
    assert out2.path == "full_pipeline"


# ── session boundary — fresh floor ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_session_boundary_starts_fresh():
    r = _ratchet()
    await r.apply(_dec("full_pipeline"), _tc(), "s1")  # s1 floor full
    # New session s2 has no floor → a low path passes (no cross-session lock).
    out = await r.apply(_dec("lightweight"), _tc(), "s2")
    assert out.path == "lightweight"


# ── bounded LRU — never grows unbounded ────────────────────────────────────
@pytest.mark.asyncio
async def test_bounded_lru_evicts_oldest_session():
    r = _ratchet(max_sessions=2)
    await r.apply(_dec("full_pipeline"), _tc(), "s1")
    await r.apply(_dec("full_pipeline"), _tc(), "s2")
    await r.apply(_dec("full_pipeline"), _tc(), "s3")  # > 2 → evict s1 (LRU)
    assert len(r._floors) == 2
    # s1's floor was evicted → a low path now passes for s1.
    out = await r.apply(_dec("lightweight"), _tc(), "s1")
    assert out.path == "lightweight"


# ── guards ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_category_is_noop():
    r = _ratchet()
    tc = TaskContext(trace_id="t", category=None, difficulty=Difficulty.MEDIUM)
    out = await r.apply(_dec("lightweight"), tc, "s")
    assert out.path == "lightweight"


# ── S5 decay hook (reserved, callable, not wired in S4) ────────────────────
@pytest.mark.asyncio
async def test_lower_floor_hook_releases_demote():
    r = _ratchet()
    await r.apply(_dec("full_pipeline"), _tc(), "s")  # floor full_pipeline
    # S5 decay would lower the floor; the hook does that.
    r.lower_floor("s", "coding", int(Difficulty.MEDIUM), "lightweight")
    out = await r.apply(_dec("lightweight"), _tc(), "s")
    assert out.path == "lightweight"  # demote now allowed (floor lowered)
