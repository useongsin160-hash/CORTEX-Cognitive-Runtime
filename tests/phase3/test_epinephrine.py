"""Phase 3 STEP 3.2 — Epinephrine trigger gate cascade.

Gate order under test (spec correction 1):
  1. unknown category    → ("unknown_category", STANDARD)
  2. category gate fail  → ("category_gate_fail", default_tier)
  3. similarity gate fail → ("similarity_gate_fail", STANDARD)
  4. activated           → ("activated", default_tier)

similarity is the mean-centered cosine in [-1, 1] — NOT the legacy
[0, 1] range. Tests intentionally probe negative values.
"""
from __future__ import annotations

import pytest

from app.core.config import EpinephrineConfig
from app.core.model_tier import ModelTier
from app.routing.neuromodulators import (
    REASON_ACTIVATED,
    REASON_CATEGORY_GATE_FAIL,
    REASON_SIMILARITY_GATE_FAIL,
    REASON_UNKNOWN_CATEGORY,
    Epinephrine,
)

DEFAULT_THRESHOLD = 0.3948


@pytest.fixture
def epinephrine() -> Epinephrine:
    return Epinephrine(EpinephrineConfig())


# ── Gate 1: unknown_category --------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_category_returns_unknown_even_with_high_similarity(epinephrine):
    activated, tier, reason = await epinephrine.decide("unknown_cat", 0.9)
    assert activated is False
    assert tier == ModelTier.STANDARD
    assert reason == REASON_UNKNOWN_CATEGORY


@pytest.mark.asyncio
async def test_unknown_category_returns_unknown_with_moderate_similarity(epinephrine):
    activated, tier, reason = await epinephrine.decide("unknown_cat", 0.5)
    assert activated is False
    assert tier == ModelTier.STANDARD
    assert reason == REASON_UNKNOWN_CATEGORY


# ── Gate 3 (activated): HIGH categories ----------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,expected_tier",
    [
        ("coding", ModelTier.DEEP_THINKING),
        ("math_logic", ModelTier.DEEP_THINKING),
        ("system_design", ModelTier.DEEP_THINKING),
        ("data_analysis", ModelTier.HEAVY),
    ],
)
async def test_high_category_with_sufficient_similarity_activates(
    epinephrine, category, expected_tier,
):
    activated, tier, reason = await epinephrine.decide(category, 0.5)
    assert activated is True
    assert tier == expected_tier
    assert reason == REASON_ACTIVATED


@pytest.mark.asyncio
async def test_high_category_below_threshold_returns_similarity_gate_fail(epinephrine):
    activated, tier, reason = await epinephrine.decide("coding", 0.3)
    assert activated is False
    assert tier == ModelTier.STANDARD
    assert reason == REASON_SIMILARITY_GATE_FAIL


# ── Gate 2: category_gate_fail (LOW categories) --------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,expected_tier",
    [
        ("general", ModelTier.LIGHTWEIGHT),
        ("writing", ModelTier.MEDIUM),
        ("game_design", ModelTier.STANDARD),
    ],
)
async def test_low_category_returns_category_gate_fail_with_default_tier(
    epinephrine, category, expected_tier,
):
    activated, tier, reason = await epinephrine.decide(category, 0.9)
    assert activated is False
    assert tier == expected_tier
    assert reason == REASON_CATEGORY_GATE_FAIL


# ── Boundary at the threshold --------------------------------------------
@pytest.mark.asyncio
async def test_threshold_boundary_just_below_does_not_activate(epinephrine):
    activated, _, reason = await epinephrine.decide(
        "coding", DEFAULT_THRESHOLD - 0.0001,
    )
    assert activated is False
    assert reason == REASON_SIMILARITY_GATE_FAIL


@pytest.mark.asyncio
async def test_threshold_boundary_equal_activates(epinephrine):
    activated, _, reason = await epinephrine.decide("coding", DEFAULT_THRESHOLD)
    assert activated is True
    assert reason == REASON_ACTIVATED


@pytest.mark.asyncio
async def test_threshold_boundary_just_above_activates(epinephrine):
    activated, _, reason = await epinephrine.decide(
        "coding", DEFAULT_THRESHOLD + 0.0001,
    )
    assert activated is True
    assert reason == REASON_ACTIVATED


# ── Negative similarities (spec correction 4: range is [-1, 1]) ----------
@pytest.mark.asyncio
async def test_high_category_with_negative_similarity_fails_confidence_gate(epinephrine):
    activated, tier, reason = await epinephrine.decide("coding", -0.1)
    assert activated is False
    assert tier == ModelTier.STANDARD
    assert reason == REASON_SIMILARITY_GATE_FAIL


@pytest.mark.asyncio
async def test_low_category_with_negative_similarity_still_category_gate_fails(epinephrine):
    activated, tier, reason = await epinephrine.decide("general", -0.5)
    assert activated is False
    assert tier == ModelTier.LIGHTWEIGHT
    assert reason == REASON_CATEGORY_GATE_FAIL


# ── Config injection (RPE prep) ------------------------------------------
@pytest.mark.asyncio
async def test_custom_config_threshold_changes_behaviour():
    relaxed = Epinephrine(EpinephrineConfig(similarity_threshold=0.2))
    activated, _, reason = await relaxed.decide("coding", 0.25)
    assert activated is True, "0.25 should clear the relaxed 0.2 threshold"
    assert reason == REASON_ACTIVATED

    strict = Epinephrine(EpinephrineConfig(similarity_threshold=0.7))
    activated, _, reason = await strict.decide("coding", 0.5)
    assert activated is False, "0.5 should fail under a 0.7 strict threshold"
    assert reason == REASON_SIMILARITY_GATE_FAIL


@pytest.mark.asyncio
async def test_config_is_frozen_and_safe_to_share():
    """EpinephrineConfig is a frozen dataclass; two Epinephrine instances
    can share one config without state bleed."""
    cfg = EpinephrineConfig()
    a = Epinephrine(cfg)
    b = Epinephrine(cfg)
    res_a = await a.decide("coding", 0.5)
    res_b = await b.decide("coding", 0.5)
    assert res_a == res_b
