"""Phase 3 STEP 3.2 / B12 — LC × Epinephrine wiring.

B12: difficulty is the sole tier authority (1:1 with ModelTier). Epinephrine is
preserved as a high-compute SIGNAL only — it stamps activated / reason and a
non-binding tier_suggestion, but no longer selects selected_tier.

LC must:
  - set selected_tier = ModelTier(difficulty) (difficulty-driven),
  - still stamp epinephrine activated / reason from the organ,
  - log an `epinephrine.decided` event with tier_suggestion (organ) AND
    selected_tier (difficulty) as ModelTier.name strings,
  - still fire-and-forget the PFC notify (no regression).
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.schemas.context import Category, EvaluationResult
from app.core.config import EpinephrineConfig
from app.core.logging import get_spinal_logger
from app.core.model_tier import ModelTier
from app.routing.lc import LocusCoeruleus
from app.routing.neuromodulators import (
    REASON_ACTIVATED,
    REASON_CATEGORY_GATE_FAIL,
    Epinephrine,
)


def _make_eval(category: Category, similarity: float, difficulty: int = 3) -> EvaluationResult:
    return EvaluationResult(
        difficulty=difficulty,
        category=category,
        confidence=0.5,
        similarity=similarity,
        classification_method="centroid",
    )


@pytest.fixture
def lc() -> LocusCoeruleus:
    return LocusCoeruleus(epinephrine=Epinephrine(EpinephrineConfig()))


@pytest.mark.asyncio
async def test_lc_stamps_epinephrine_signal_tier_from_difficulty(lc):
    # difficulty default 3 → selected_tier STANDARD (1:1), regardless of the
    # organ's DEEP_THINKING suggestion for coding. Organ signal still stamped.
    ctx = await lc.process("design a payments system", _make_eval("coding", 0.5))
    assert ctx.epinephrine_active is True
    assert ctx.selected_tier == ModelTier.STANDARD
    assert ctx.epinephrine_reason == REASON_ACTIVATED


@pytest.mark.asyncio
async def test_lc_low_category_records_category_gate_fail(lc):
    # general → organ category gate fails (active False / reason gate_fail),
    # but selected_tier is still difficulty-driven (3 → STANDARD).
    ctx = await lc.process("recommend a film", _make_eval("general", 0.9))
    assert ctx.epinephrine_active is False
    assert ctx.selected_tier == ModelTier.STANDARD
    assert ctx.epinephrine_reason == REASON_CATEGORY_GATE_FAIL


@pytest.mark.asyncio
async def test_lc_logs_epinephrine_decided_event_with_tier_as_string(lc):
    logger = get_spinal_logger()
    ctx = await lc.process("solve this theorem", _make_eval("math_logic", 0.6))
    events = logger.get_trace(ctx.trace_id)
    eph_events = [e for e in events if e.event_type == "epinephrine.decided"]
    assert len(eph_events) == 1
    payload = eph_events[0].payload
    assert payload["category"] == "math_logic"
    assert payload["activated"] is True
    assert payload["reason"] == REASON_ACTIVATED
    # tier_suggestion = organ's (non-binding) view; selected_tier = difficulty(3).
    # Both must be .name strings, never the IntEnum int — API serialization rule.
    assert payload["tier_suggestion"] == "DEEP_THINKING"
    assert payload["selected_tier"] == "STANDARD"
    assert isinstance(payload["tier_suggestion"], str)
    assert isinstance(payload["selected_tier"], str)


@pytest.mark.asyncio
async def test_lc_without_epinephrine_tier_still_from_difficulty():
    """No organ injected (Phase 2 construction) — selected_tier is STILL the
    difficulty-derived tier (B12: difficulty is the sole authority)."""
    lc_no_eph = LocusCoeruleus()
    ctx = await lc_no_eph.process("anything", _make_eval("coding", 0.9, difficulty=1))
    assert ctx.epinephrine_active is False
    assert ctx.selected_tier == ModelTier.LIGHTWEIGHT  # difficulty 1, no organ
    assert ctx.epinephrine_reason is None


@pytest.mark.asyncio
async def test_lc_pfc_dispatch_still_fires(lc):
    logger = get_spinal_logger()
    ctx = await lc.process("anything", _make_eval("coding", 0.5))
    await asyncio.sleep(0)  # let create_task drain
    events = logger.get_trace(ctx.trace_id)
    assert any(e.event_type == "pfc_stub_called" for e in events), (
        "PFC dispatch regressed — fire-and-forget must still emit"
    )
