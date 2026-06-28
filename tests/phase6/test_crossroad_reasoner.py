"""B8 — CrossroadReasoner tests (fake store + fake runner + deterministic rng).

Covers: crossroad detection (within/outside margin, demote/promote, edge clamp),
unlearned no-op, disabled no-op, mode blocking (emergency), probability gating,
the explore context (adjacent band + sub-trace + epinephrine), fire-and-forget,
fail-open, CancelledError re-raise, and config validation. No swarm / e5.
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import Difficulty, TaskContext
from app.core.logging import SpinalLogger
from app.routing.crossroad import CrossroadConfig, CrossroadReasoner
from app.routing.skip_router import RouteDecision


class _FakeStore:
    def __init__(self, weight: float | None):
        self._weight = weight
        self.calls: list[tuple] = []

    async def read_weight(self, session_id, category, difficulty):
        self.calls.append((session_id, category, difficulty))
        return self._weight


class _RecordingRunner:
    def __init__(self, *, raise_exc: BaseException | None = None):
        self.calls: list[dict] = []
        self._raise = raise_exc

    async def __call__(self, task_context, query_features=None, *, trace_id, session_id):
        self.calls.append(
            {
                "task_context": task_context,
                "query_features": query_features,
                "trace_id": trace_id,
                "session_id": session_id,
            }
        )
        if self._raise is not None:
            raise self._raise


def _reasoner(weight, *, enabled=True, rng_value=0.0, margin=0.05,
              stable_prob=0.10, runner=None):
    runner = runner if runner is not None else _RecordingRunner()
    reasoner = CrossroadReasoner(
        store=_FakeStore(weight),
        explore_runner=runner,
        logger=SpinalLogger(),
        config=CrossroadConfig(
            enabled=enabled, stable_probability=stable_prob, margin=margin
        ),
        rng=lambda: rng_value,
    )
    return reasoner, runner


def _ctx(path="standard", *, difficulty=Difficulty.MEDIUM, category="coding",
         epi=False, ne=False, trace="t1"):
    return TaskContext(
        trace_id=trace,
        category=category,
        difficulty=difficulty,
        route_path=path,
        epinephrine_active=epi,
        ne_boost=ne,
    )


def _decision(path):
    return RouteDecision(path=path, reason="x")


async def _drain(reasoner):
    """Await any spawned background explore tasks."""
    await asyncio.gather(*list(reasoner._background_tasks))


# ── crossroad detection ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fires_at_demote_crossroad():
    r, runner = _reasoner(weight=0.42)  # |0.42-0.4|=0.02 <= 0.05
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)
    assert len(runner.calls) == 1
    ctx = runner.calls[0]["task_context"]
    assert ctx.route_path == "lightweight"  # demote: standard → lightweight
    assert runner.calls[0]["trace_id"] == "t1::cr_explore"


@pytest.mark.asyncio
async def test_fires_at_promote_crossroad_sets_epinephrine():
    r, runner = _reasoner(weight=0.68)  # |0.68-0.7|=0.02 <= 0.05
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)
    assert len(runner.calls) == 1
    ctx = runner.calls[0]["task_context"]
    assert ctx.route_path == "full_pipeline"  # promote: standard → full_pipeline
    assert ctx.epinephrine_active is True  # band-consistent
    assert ctx.epinephrine_reason == "limit_break"


@pytest.mark.asyncio
async def test_no_fire_outside_margin():
    r, runner = _reasoner(weight=0.55)  # mid band, near neither threshold
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_no_fire_unlearned_cell():
    r, runner = _reasoner(weight=None)
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


# ── edge clamp ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_edge_clamp_lightweight_cannot_demote():
    r, runner = _reasoner(weight=0.42)  # near demote, but already at bottom
    await r.maybe_explore(_ctx("lightweight"), _decision("lightweight"), "s1")
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_edge_clamp_full_pipeline_cannot_promote():
    r, runner = _reasoner(weight=0.68)  # near promote, but already at top
    await r.maybe_explore(_ctx("full_pipeline"), _decision("full_pipeline"), "s1")
    await _drain(r)
    assert runner.calls == []


# ── gating: disabled / probability / mode ───────────────────────────────────
@pytest.mark.asyncio
async def test_disabled_is_noop():
    r, runner = _reasoner(weight=0.42, enabled=False)
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_probability_gate_blocks():
    r, runner = _reasoner(weight=0.42, rng_value=0.99)  # >= 0.10 → no fire
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_emergency_mode_epinephrine_blocks():
    r, runner = _reasoner(weight=0.42)
    await r.maybe_explore(_ctx("standard", epi=True), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_emergency_mode_ne_boost_blocks():
    r, runner = _reasoner(weight=0.42)
    await r.maybe_explore(_ctx("standard", ne=True), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_no_category_is_noop():
    r, runner = _reasoner(weight=0.42)
    await r.maybe_explore(_ctx("standard", category=None), _decision("standard"), "s1")
    await _drain(r)
    assert runner.calls == []


# ── fail-open / cancellation ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_explore_runner_failure_is_fail_open():
    runner = _RecordingRunner(raise_exc=RuntimeError("boom"))
    r, _ = _reasoner(weight=0.42, runner=runner)
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    await _drain(r)  # must not raise — _run_explore swallows the error
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_explore_cancelled_is_reraised():
    runner = _RecordingRunner(raise_exc=asyncio.CancelledError())
    r, _ = _reasoner(weight=0.42, runner=runner)
    await r.maybe_explore(_ctx("standard"), _decision("standard"), "s1")
    with pytest.raises(asyncio.CancelledError):
        await _drain(r)


# ── explore mode (B10): pfc_explore raises the fire probability ─────────────
@pytest.mark.asyncio
async def test_explore_mode_fires_at_explore_probability():
    # rng 0.3 >= stable 0.10 (stable would NOT fire) but < explore 0.50 → fires.
    r, runner = _reasoner(weight=0.42, rng_value=0.3)
    await r.maybe_explore(
        _ctx("standard"), _decision("standard"), "s1", pfc_explore=True,
    )
    await _drain(r)
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_stable_mode_does_not_fire_at_explore_threshold():
    # Same rng 0.3 without pfc_explore → stable mode (0.10) → no fire.
    r, runner = _reasoner(weight=0.42, rng_value=0.3)
    await r.maybe_explore(
        _ctx("standard"), _decision("standard"), "s1", pfc_explore=False,
    )
    await _drain(r)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_explore_mode_still_blocked_by_emergency():
    # Emergency (epinephrine/NE) overrides explore mode → CR off.
    r, runner = _reasoner(weight=0.42, rng_value=0.0)
    await r.maybe_explore(
        _ctx("standard", epi=True), _decision("standard"), "s1", pfc_explore=True,
    )
    await _drain(r)
    assert runner.calls == []


# ── config validation ───────────────────────────────────────────────────────
def test_config_validation():
    with pytest.raises(ValueError, match="stable_probability"):
        CrossroadConfig(stable_probability=1.5)
    with pytest.raises(ValueError, match="margin"):
        CrossroadConfig(margin=-0.1)
