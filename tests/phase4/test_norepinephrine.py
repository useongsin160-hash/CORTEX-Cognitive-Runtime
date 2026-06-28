"""Phase 4 STEP 1 — Norepinephrine parameter modulation."""
from __future__ import annotations

import pytest

from app.core.model_tier import ModelTier
from app.execution.params import GenerationParams
from app.routing.neuromodulators import (
    NE_TEMPERATURE_CEILING,
    NE_TOP_K_FLOOR,
    Norepinephrine,
)


@pytest.fixture
def ne() -> Norepinephrine:
    return Norepinephrine()


# ── should_activate — B12 5단계: difficulty >= 4 (VERY_HARD 이상) ────────
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "difficulty,expected",
    [(1, False), (2, False), (3, False), (4, True), (5, True)],
)
async def test_should_activate(ne, difficulty, expected):
    assert await ne.should_activate(difficulty) is expected


# ── modify_params — ne_active False ─────────────────────────────────────
@pytest.mark.asyncio
async def test_inactive_leaves_params_unmodified(ne):
    base = GenerationParams(temperature=0.7, top_k=40)
    out = await ne.modify_params(base, ModelTier.DEEP_THINKING, ne_active=False)
    assert out.temperature == 0.7
    assert out.top_k == 40
    assert out.ne_applied is False
    assert out.ne_reason is None


# ── modify_params — active + tier >= STANDARD → applied ─────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [ModelTier.STANDARD, ModelTier.HEAVY, ModelTier.DEEP_THINKING])
async def test_active_with_standard_or_above_applies(ne, tier):
    base = GenerationParams(temperature=0.7, top_k=40)
    out = await ne.modify_params(base, tier, ne_active=True)
    assert out.temperature <= NE_TEMPERATURE_CEILING
    assert out.top_k >= NE_TOP_K_FLOOR
    assert out.ne_applied is True
    assert out.ne_reason == "high_difficulty"


# ── modify_params — active + tier < STANDARD → mismatch ─────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [ModelTier.LIGHTWEIGHT, ModelTier.MEDIUM])
async def test_active_with_low_tier_is_mismatch(ne, tier):
    base = GenerationParams(temperature=0.7, top_k=40)
    out = await ne.modify_params(base, tier, ne_active=True)
    # 변조 없음 — 강제 승격 금지
    assert out.temperature == 0.7
    assert out.top_k == 40
    assert out.ne_applied is False
    assert out.ne_reason == "tier_mismatch"


# ── min/max semantics ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_existing_lower_temperature_is_preserved(ne):
    base = GenerationParams(temperature=0.05, top_k=40)
    out = await ne.modify_params(base, ModelTier.DEEP_THINKING, ne_active=True)
    # min(0.05, 0.1) = 0.05 — 더 낮은 값 유지
    assert out.temperature == 0.05


@pytest.mark.asyncio
async def test_existing_higher_top_k_is_preserved(ne):
    base = GenerationParams(temperature=0.7, top_k=100)
    out = await ne.modify_params(base, ModelTier.HEAVY, ne_active=True)
    # max(100, 80) = 100 — 더 높은 값 유지
    assert out.top_k == 100
