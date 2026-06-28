"""B7 — BasalGanglia production wiring tests.

Exercises the routes-level advisory helper `_basal_ganglia_apply` directly
(no create_app build → avoids the e5/safetensors memory flake). Proves:

- the advisor runs on real TaskContext snapshots and emits a bg.evaluated trace
  (applied=False),
- the recommendation is NEVER read back into routing (route_path unchanged),
- the honest None/0 degradation still yields a recommendation from synapse alone
  (no invented PFC/LC/RPE input),
- the whole pass is fail-open (bad snapshot / missing advisor never break the
  request) and never mutates its inputs.

Each test uses a unique trace_id so the process-wide SpinalLogger singleton
cannot leak events across tests.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import _basal_ganglia_apply
from app.api.schemas.context import Difficulty, TaskContext
from app.basal_ganglia.advisor import BasalGangliaAdvisor
from app.core.logging import SpinalLogger
from app.routing.skip_router import RouteDecision


def _state(advisor):
    """Observe-only state: no .settings → bg_apply_enabled falls back to False."""
    return SimpleNamespace(basal_ganglia=advisor)


def _apply_state(advisor, *, enabled=True):
    """Apply-mode state: carries settings.bg_apply_enabled (C2 promote-only)."""
    return SimpleNamespace(
        basal_ganglia=advisor,
        settings=SimpleNamespace(bg_apply_enabled=enabled),
    )


def _decision(path):
    return RouteDecision(path=path, reason="test-baseline")


def _task_context(
    trace_id, *, synapse=None, route_path="lightweight",
    difficulty=2, ne_boost=False,
):
    return TaskContext(
        trace_id=trace_id,
        category="coding",
        difficulty=Difficulty(difficulty),
        synapse_snapshot={"coding": 0.6} if synapse is None else synapse,
        route_path=route_path,
        ne_boost=ne_boost,
    )


def _bg_events(logger, trace_id, event_type):
    return [
        e
        for e in logger.get_trace(trace_id)
        if e.module_name == "basal_ganglia" and e.event_type == event_type
    ]


# ── runs + logs bg.evaluated (applied=False) ────────────────────────────────
@pytest.mark.asyncio
async def test_observe_emits_bg_evaluated_applied_false():
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "b7-wire-evaluated"
    tc = _task_context(trace_id)

    await _basal_ganglia_apply(
        _state(advisor), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    events = _bg_events(logger, trace_id, "bg.evaluated")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["applied"] is False
    assert payload["category"] == "coding"
    # synapse-only degradation still produces a recommendation.
    assert payload["selected_type"] is not None


# ── recommendation is never consumed: route_path unchanged ──────────────────
@pytest.mark.asyncio
async def test_observe_does_not_touch_route_path():
    advisor = BasalGangliaAdvisor(logger=SpinalLogger())
    trace_id = "b7-wire-routepath"
    tc = _task_context(trace_id, route_path="lightweight")

    await _basal_ganglia_apply(
        _state(advisor), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    assert tc.route_path == "lightweight"  # advisory only — never rerouted


# ── honest None/0 degradation: synapse-only recommendation ──────────────────
@pytest.mark.asyncio
async def test_observe_runs_on_synapse_only_no_invented_input():
    """pfc_decision=None + no counter + ne_boost False → pfc None, lc ne_level 0.0
    (the real bool), rpe 0/0 — all real, none fabricated. BG runs on the difficulty
    anchor (difficulty 2 → standard band) lightly de-escalated by the familiar
    synapse 0.6, landing on swarm_minimal (the redesigned demand-match)."""
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "b7-wire-synapse-only"
    tc = _task_context(trace_id, synapse={"coding": 0.6})

    await _basal_ganglia_apply(
        _state(advisor), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    events = _bg_events(logger, trace_id, "bg.evaluated")
    assert len(events) == 1
    # difficulty 2 anchors at the standard band; synapse 0.6 nudges demand down a
    # little but stays closest to swarm_minimal's level.
    assert events[0].payload["selected_type"] == "swarm_minimal"


# ── input is not mutated (read-only) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_observe_does_not_mutate_synapse_snapshot():
    advisor = BasalGangliaAdvisor(logger=SpinalLogger())
    trace_id = "b7-wire-nomutate"
    source = {"coding": 0.6, "writing": 0.4}
    tc = _task_context(trace_id, synapse=source)

    await _basal_ganglia_apply(
        _state(advisor), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    assert source == {"coding": 0.6, "writing": 0.4}


# ── fail-open: out-of-range snapshot must not break the request ─────────────
@pytest.mark.asyncio
async def test_observe_fail_open_on_bad_snapshot():
    """A synapse weight outside [0,1] makes the context builder raise; the
    helper swallows it (CancelledError excepted) so the request is unharmed."""
    advisor = BasalGangliaAdvisor(logger=SpinalLogger())
    trace_id = "b7-wire-badsnap"
    tc = _task_context(trace_id, synapse={"coding": 1.5}, route_path="full_pipeline")

    # must not raise.
    await _basal_ganglia_apply(
        _state(advisor), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    assert tc.route_path == "full_pipeline"  # unchanged


# ── advisor absent → no-op, no raise ────────────────────────────────────────
@pytest.mark.asyncio
async def test_observe_no_advisor_is_noop():
    trace_id = "b7-wire-noadvisor"
    tc = _task_context(trace_id, route_path="lightweight")

    # state with no basal_ganglia attribute.
    await _basal_ganglia_apply(
        SimpleNamespace(), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )
    # explicit None advisor.
    await _basal_ganglia_apply(
        _state(None), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    assert tc.route_path == "lightweight"


# ── advisor None never emits a trace ────────────────────────────────────────
@pytest.mark.asyncio
async def test_observe_no_advisor_emits_nothing():
    logger = SpinalLogger()
    trace_id = "b7-wire-noadvisor-notrace"
    tc = _task_context(trace_id)

    await _basal_ganglia_apply(
        _state(None), tc, _decision(tc.route_path), None,
        trace_id=trace_id, session_id="s1",
    )

    assert _bg_events(logger, trace_id, "bg.evaluated") == []


# ════════════════════════════════════════════════════════════════════════════
# C2 — promote-only apply (bg_apply_enabled). The recommendation now adjusts
# route_path, but ONLY upward; never below the post-ratchet band.
# ════════════════════════════════════════════════════════════════════════════


# ── promote: a heavier BG band raises route_path + the returned decision ─────
@pytest.mark.asyncio
async def test_apply_promotes_route_path_and_decision():
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "c2-apply-promote"
    # difficulty 2 anchors at the standard band → BG recommends swarm_minimal
    # (standard); from a lightweight decision that is a promotion.
    tc = _task_context(trace_id, synapse={"coding": 0.6}, route_path="lightweight")

    result = await _basal_ganglia_apply(
        _apply_state(advisor), tc, _decision("lightweight"), None,
        trace_id=trace_id, session_id="s1",
    )

    assert tc.route_path == "standard"           # promoted
    assert result.path == "standard"             # decision synced (CR sees this)
    applied = _bg_events(logger, trace_id, "bg.applied")
    assert len(applied) == 1
    assert applied[0].payload["from_path"] == "lightweight"
    assert applied[0].payload["to_path"] == "standard"
    assert applied[0].payload["promote_only"] is True


# ── promote to full_pipeline re-derives epinephrine (limit-break) ───────────
@pytest.mark.asyncio
async def test_apply_promote_to_full_pipeline_rederives_epinephrine():
    advisor = BasalGangliaAdvisor(logger=SpinalLogger())
    trace_id = "c2-apply-epi"
    # low synapse + NE escalates demand to swarm_full (full_pipeline).
    tc = _task_context(
        trace_id, synapse={"coding": 0.1}, route_path="standard", ne_boost=True,
    )

    result = await _basal_ganglia_apply(
        _apply_state(advisor), tc, _decision("standard"), None,
        trace_id=trace_id, session_id="s1",
    )

    assert result.path == "full_pipeline"
    assert tc.route_path == "full_pipeline"
    assert tc.epinephrine_active is True
    assert tc.epinephrine_reason == "limit_break"


# ── promote-only: a lighter BG band NEVER demotes (high-diff baseline safe) ──
@pytest.mark.asyncio
async def test_apply_never_demotes_below_decision():
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "c2-apply-nodemote"
    # familiar (synapse 0.95) → BG recommends a lighter band (swarm_minimal /
    # standard), but the decision is already full_pipeline → no demotion.
    tc = _task_context(trace_id, synapse={"coding": 0.95}, route_path="full_pipeline")

    result = await _basal_ganglia_apply(
        _apply_state(advisor), tc, _decision("full_pipeline"), None,
        trace_id=trace_id, session_id="s1",
    )

    assert tc.route_path == "full_pipeline"      # unchanged — no demote
    assert result.path == "full_pipeline"
    assert _bg_events(logger, trace_id, "bg.applied") == []  # nothing applied


# ── flag off → observe-only (route_path untouched even with a heavier band) ──
@pytest.mark.asyncio
async def test_apply_disabled_is_observe_only():
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "c2-apply-off"
    tc = _task_context(trace_id, synapse={"coding": 0.6}, route_path="lightweight")

    result = await _basal_ganglia_apply(
        _apply_state(advisor, enabled=False), tc, _decision("lightweight"), None,
        trace_id=trace_id, session_id="s1",
    )

    assert tc.route_path == "lightweight"        # observe-only — not applied
    assert result.path == "lightweight"
    assert _bg_events(logger, trace_id, "bg.applied") == []
    # evaluate still ran (telemetry preserved).
    assert len(_bg_events(logger, trace_id, "bg.evaluated")) == 1


# ── model hard-lock holds in apply mode: ActionSelectionDecision.applied=False ─
@pytest.mark.asyncio
async def test_apply_mode_keeps_action_decision_applied_false():
    logger = SpinalLogger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "c2-apply-hardlock"
    tc = _task_context(trace_id, synapse={"coding": 0.6}, route_path="lightweight")

    await _basal_ganglia_apply(
        _apply_state(advisor), tc, _decision("lightweight"), None,
        trace_id=trace_id, session_id="s1",
    )

    # The ActionSelectionDecision rail stays False even though route_path was
    # applied — apply adjusts the RouteDecision, never that model flag.
    evaluated = _bg_events(logger, trace_id, "bg.evaluated")
    assert len(evaluated) == 1
    assert evaluated[0].payload["applied"] is False
