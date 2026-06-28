"""Phase 6 STEP 5.1 invariants — updated for B7 one-way wiring.

STEP 5.1 landed BG fully isolated. B7 wires it into production one-way
(main/routes → BG, advisory/telemetry-only). This file now asserts that
post-B7 truth while keeping every other STEP 5.1 invariant intact:
- routes.py / main.py NOW reference BG (B7 wiring); swarm.py / pfc.py / lc.py /
  rpe.* / ifom stay BG-free (one-way: BG never imported by the inner layers)
- app.state.basal_ganglia is the wired advisor; no bg_* state / schema fields
  (telemetry-only — recommendation surfaces via bg.evaluated)
- ActionSelectionDecision.applied is still hard-locked to False at the type level
- No new RPE mutation target in app/rpe/models.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _src(rel: str) -> str:
    p = APP_ROOT / rel
    assert p.exists(), f"{p} not found"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-level absence of BasalGanglia references
# ---------------------------------------------------------------------------


def test_routes_references_basal_ganglia_b7():
    """B7 — routes wires the BG advisor (one-way: routes → BG)."""
    src = _src("api/routes.py")
    assert "basal_ganglia" in src.lower()


def test_swarm_no_basal_ganglia_reference():
    src = _src("execution/swarm.py")
    assert "basal_ganglia" not in src.lower()


def test_main_references_basal_ganglia_b7():
    """B7 — main injects the BG advisor onto app.state (one-way: main → BG)."""
    src = _src("main.py")
    assert "basal_ganglia" in src.lower()


def test_pfc_no_basal_ganglia_reference():
    src = _src("routing/pfc.py")
    assert "basal_ganglia" not in src.lower()


def test_lc_no_basal_ganglia_reference():
    src = _src("routing/lc.py")
    assert "basal_ganglia" not in src.lower()


def test_rpe_pipeline_no_basal_ganglia_reference():
    src = _src("rpe/pipeline.py")
    assert "basal_ganglia" not in src.lower()


def test_rpe_service_no_basal_ganglia_reference():
    src = _src("rpe/service.py")
    assert "basal_ganglia" not in src.lower()


def test_ifom_no_basal_ganglia_reference():
    src = _src("memory/ifom.py")
    assert "basal_ganglia" not in src.lower()


# ---------------------------------------------------------------------------
# RPE mutation target unchanged
# ---------------------------------------------------------------------------


def test_rpe_mutation_targets_unchanged():
    """STEP 5.1 must NOT add a new RPE active target. Only synapse_weight + ifom_ttl."""
    from app.rpe.models import _ACTIVE_PROPOSAL_TARGETS
    assert _ACTIVE_PROPOSAL_TARGETS == frozenset({"synapse_weight", "ifom_ttl"})


# ---------------------------------------------------------------------------
# ActionSelectionDecision.applied hard-locked to False
# ---------------------------------------------------------------------------


def test_decision_applied_cannot_be_true():
    from app.basal_ganglia.models import (
        ActionSelectionContext,
        ActionSelectionDecision,
    )
    ctx = ActionSelectionContext(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        pfc_active=False, pfc_cue_type=None, pfc_confidence=None,
        pfc_intent_category=None, lc_ne_level=None, lc_intent_label=None,
    )
    with pytest.raises(ValueError, match="applied must be False"):
        ActionSelectionDecision(
            context=ctx, candidates=(), selected=None,
            confidence=0.0, reason="x", applied=True,
        )


def test_decision_applied_default_false():
    from app.basal_ganglia.models import (
        ActionSelectionContext,
        ActionSelectionDecision,
    )
    ctx = ActionSelectionContext(
        trace_id="tr", session_id=None, category=None, difficulty=0,
        pfc_active=False, pfc_cue_type=None, pfc_confidence=None,
        pfc_intent_category=None, lc_ne_level=None, lc_intent_label=None,
    )
    decision = ActionSelectionDecision(
        context=ctx, candidates=(), selected=None,
        confidence=0.0, reason="no_candidates",
    )
    assert decision.applied is False


# ---------------------------------------------------------------------------
# main.py state unchanged at runtime
# ---------------------------------------------------------------------------


def test_main_state_has_basal_ganglia_advisor_b7(app_client):
    """B7 — app.state.basal_ganglia is the wired advisor. Telemetry-only: no
    bg_* state attrs are added (recommendation surfaces via bg.evaluated only)."""
    from app.basal_ganglia.advisor import BasalGangliaAdvisor
    assert isinstance(app_client.app.state.basal_ganglia, BasalGangliaAdvisor)
    bg_prefixed = [a for a in dir(app_client.app.state) if "bg_" in a.lower()]
    assert bg_prefixed == [], f"Unexpected bg_* attrs in app.state: {bg_prefixed}"


# ---------------------------------------------------------------------------
# routes.py / SwarmTrace / QueryResponse unchanged at API level
# ---------------------------------------------------------------------------


def test_query_response_schema_unchanged():
    """No new fields added to QueryResponse for BG."""
    from app.api.schemas.response import QueryResponse
    field_names = set(QueryResponse.model_fields.keys())
    assert "basal_ganglia" not in field_names
    assert "bg_decision" not in field_names
    assert "bg_selected" not in field_names


def test_swarm_trace_schema_unchanged():
    """No new fields added to SwarmTrace for BG."""
    from app.api.schemas.response import SwarmTrace
    field_names = set(SwarmTrace.model_fields.keys())
    assert "basal_ganglia" not in field_names
    assert "bg_decision" not in field_names
