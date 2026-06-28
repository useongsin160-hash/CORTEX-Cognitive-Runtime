"""Phase 6 STEP 1 — RPE reward sources tests."""

from __future__ import annotations

import pytest

from app.rpe.models import RPEContext
from app.rpe.sources import (
    HeuristicOutcomeSource,
    MockRewardSource,
    RewardSourceProtocol,
)


def _ctx(**overrides) -> RPEContext:
    defaults = {"trace_id": "trace-1"}
    defaults.update(overrides)
    return RPEContext(**defaults)


class TestProtocol:
    def test_mock_matches_protocol(self) -> None:
        assert isinstance(MockRewardSource(), RewardSourceProtocol)

    def test_heuristic_matches_protocol(self) -> None:
        assert isinstance(HeuristicOutcomeSource(), RewardSourceProtocol)


class TestMockRewardSource:
    @pytest.mark.asyncio
    async def test_default_returns_neutral(self) -> None:
        src = MockRewardSource()
        reward = await src.compute_reward(_ctx())
        assert reward.source == "mock"
        assert reward.expected_reward == 0.5
        assert reward.actual_reward == 0.5
        assert reward.confidence == 1.0
        assert reward.prediction_error == 0.0

    @pytest.mark.asyncio
    async def test_trace_mapping(self) -> None:
        src = MockRewardSource(reward_map={"trace-x": (0.2, 0.9)})
        reward = await src.compute_reward(_ctx(trace_id="trace-x"))
        assert reward.expected_reward == 0.2
        assert reward.actual_reward == 0.9
        assert reward.confidence == 1.0
        assert reward.prediction_error == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_unknown_trace_falls_back_to_default(self) -> None:
        src = MockRewardSource(
            reward_map={"trace-x": (0.2, 0.9)},
            default=(0.4, 0.4),
        )
        reward = await src.compute_reward(_ctx(trace_id="trace-other"))
        assert reward.expected_reward == 0.4
        assert reward.actual_reward == 0.4

    @pytest.mark.asyncio
    async def test_invalid_reward_map_value_raises_via_reward_validation(self) -> None:
        src = MockRewardSource(reward_map={"trace-bad": (1.5, 0.5)})
        with pytest.raises(ValueError, match="expected_reward"):
            await src.compute_reward(_ctx(trace_id="trace-bad"))


class TestHeuristicOutcomeSource:
    @pytest.mark.asyncio
    async def test_normal_response_no_label_stays_near_baseline(self) -> None:
        # No error, no timeout, no expected label, no latency info.
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(response_source="generated"))
        assert reward.source == "heuristic"
        assert reward.expected_reward == 0.5
        assert reward.actual_reward == 0.5
        assert reward.confidence == 0.3
        assert reward.prediction_error == 0.0

    @pytest.mark.asyncio
    async def test_fast_latency_weak_positive(self) -> None:
        src = HeuristicOutcomeSource(latency_threshold_ms=100.0)
        reward = await src.compute_reward(
            _ctx(latency_ms=50.0, response_source="generated"),
        )
        assert reward.actual_reward == pytest.approx(0.55)
        assert reward.confidence == 0.3

    @pytest.mark.asyncio
    async def test_slow_latency_no_bonus(self) -> None:
        src = HeuristicOutcomeSource(latency_threshold_ms=100.0)
        reward = await src.compute_reward(
            _ctx(latency_ms=500.0, response_source="generated"),
        )
        assert reward.actual_reward == 0.5

    @pytest.mark.asyncio
    async def test_error_negative(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(error_occurred=True))
        assert reward.actual_reward == pytest.approx(0.25)
        # B13: a failure is itself a confident signal (0.3 + 0.15).
        assert reward.confidence == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_timeout_negative(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(timeout_occurred=True))
        assert reward.actual_reward == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_fallback_negative(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(response_source="fallback"))
        assert reward.actual_reward == pytest.approx(0.30)

    @pytest.mark.asyncio
    async def test_cache_hit_is_neutral(self) -> None:
        src = HeuristicOutcomeSource()
        for cache_source in ("exact_cache", "semantic_cache"):
            reward = await src.compute_reward(_ctx(response_source=cache_source))
            assert reward.actual_reward == 0.5, cache_source

    @pytest.mark.asyncio
    async def test_tier_1_5_is_neutral(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(response_source="tier_1_5"))
        assert reward.actual_reward == 0.5

    @pytest.mark.asyncio
    async def test_expected_behavior_matched_weak_positive(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(extra=(("expected_behavior_matched", True),)),
        )
        assert reward.actual_reward == pytest.approx(0.60)
        assert reward.confidence == 0.5

    @pytest.mark.asyncio
    async def test_expected_continuation_requires_bypass_and_hint(self) -> None:
        src = HeuristicOutcomeSource()
        # Missing pfc_hint_applied → no bonus.
        reward = await src.compute_reward(
            _ctx(
                continuation_bypass=True,
                pfc_hint_applied=False,
                extra=(("expected_continuation", True),),
            )
        )
        assert reward.actual_reward == 0.5
        assert reward.confidence == 0.3

    @pytest.mark.asyncio
    async def test_expected_continuation_bonus(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(
                continuation_bypass=True,
                pfc_hint_applied=True,
                extra=(("expected_continuation", True),),
            )
        )
        assert reward.actual_reward == pytest.approx(0.55)
        assert reward.confidence == 0.5

    @pytest.mark.asyncio
    async def test_error_clamps_to_zero_lower_bound(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(
                error_occurred=True,
                timeout_occurred=True,
                response_source="fallback",
            )
        )
        # 0.5 - 0.25 - 0.25 - 0.20 = -0.20 → clamped to 0.0
        assert reward.actual_reward == 0.0

    @pytest.mark.asyncio
    async def test_label_alone_gives_confidence_half(self) -> None:
        # Backward-compat: an expected_* label alone still yields confidence 0.5.
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(
                continuation_bypass=True,
                pfc_hint_applied=True,
                extra=(
                    ("expected_behavior_matched", True),
                    ("expected_continuation", True),
                ),
            )
        )
        assert reward.confidence == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_confidence_bounded_at_point_six(self) -> None:
        # B13: corroboration can stack past 0.6 but is capped — process success
        # is never as confident as verified correctness (reserved for ground truth).
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(
                planner_ok=True,
                generator_ok=True,
                context_ok=True,
                clean_finish=True,
                context_mean_similarity=0.6,
                extra=(("expected_behavior_matched", True),),
            )
        )
        # raw corroboration: 0.3 +0.15 +0.10 +0.05 +0.20 = 0.80 → capped 0.6
        assert reward.confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_error_overrides_fast_latency_bonus(self) -> None:
        src = HeuristicOutcomeSource(latency_threshold_ms=100.0)
        reward = await src.compute_reward(
            _ctx(latency_ms=10.0, error_occurred=True),
        )
        # No latency bonus when error occurred.
        assert reward.actual_reward == pytest.approx(0.25)

    def test_invalid_latency_threshold(self) -> None:
        with pytest.raises(ValueError, match="latency_threshold_ms"):
            HeuristicOutcomeSource(latency_threshold_ms=0.0)

    def test_invalid_baseline(self) -> None:
        with pytest.raises(ValueError, match="baseline_expected"):
            HeuristicOutcomeSource(baseline_expected=1.5)


# Production active gate (ActiveMutationConfig defaults) — held constant by B13.
_GATE_MIN_CONF = 0.5
_GATE_MIN_ABS_PE = 0.3


class TestHeuristicSuccessSignalsB13:
    """B13 — restored observable success signals. The reward source must now see
    process quality (not only failure) so promote learning can cross the gate."""

    @pytest.mark.asyncio
    async def test_clean_pipeline_stages_positive(self) -> None:
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(planner_ok=True, generator_ok=True, context_ok=True),
        )
        assert reward.actual_reward == pytest.approx(0.62)  # 0.5 + 3*0.04
        assert reward.confidence == pytest.approx(0.45)     # 0.3 + 0.15 (clean pipeline)

    @pytest.mark.asyncio
    async def test_relevance_scales_actual(self) -> None:
        src = HeuristicOutcomeSource()
        # similarity 0.25 → frac 0.5 → +0.08; >= 0.3 false so no confidence bump.
        reward = await src.compute_reward(_ctx(context_mean_similarity=0.25))
        assert reward.actual_reward == pytest.approx(0.58)
        assert reward.confidence == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_well_grounded_success_crosses_gate(self) -> None:
        """The B13 promote case: a clean, well-grounded success clears BOTH the
        confidence and the |PE| gate (production previously could not)."""
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(
                planner_ok=True, generator_ok=True, context_ok=True,
                clean_finish=True, context_mean_similarity=0.6,
            )
        )
        # actual 0.5 +0.12 +0.08 +0.16 = 0.86 → PE +0.36
        assert reward.actual_reward == pytest.approx(0.86)
        assert reward.prediction_error >= _GATE_MIN_ABS_PE
        assert reward.confidence >= _GATE_MIN_CONF

    @pytest.mark.asyncio
    async def test_partial_success_stays_sub_gate(self) -> None:
        """Indiscriminate-praise guard: planner+generator ok but no context / no
        clean finish / no relevance must NOT cross the gate (stays neutral)."""
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(planner_ok=True, generator_ok=True))
        assert reward.actual_reward == pytest.approx(0.58)  # PE +0.08
        assert reward.prediction_error < _GATE_MIN_ABS_PE
        assert reward.confidence < _GATE_MIN_CONF           # 0.3 (no clean-pipeline bonus)

    @pytest.mark.asyncio
    async def test_failure_now_clears_confidence_gate(self) -> None:
        """Demote was ALSO blocked in production (confidence stuck at 0.3); a
        clear failure now corroborates confidence past the gate."""
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(
            _ctx(error_occurred=True, timeout_occurred=True, response_source="fallback"),
        )
        assert reward.actual_reward == 0.0          # clamped
        assert reward.prediction_error <= -_GATE_MIN_ABS_PE
        assert reward.confidence >= _GATE_MIN_CONF  # 0.3 +0.15 +0.15 +0.10 → cap 0.6

    @pytest.mark.asyncio
    async def test_default_context_unchanged_actual(self) -> None:
        """A context with no new signals set behaves as before (defaults → 0)."""
        src = HeuristicOutcomeSource()
        reward = await src.compute_reward(_ctx(response_source="generated"))
        assert reward.actual_reward == 0.5
        assert reward.confidence == 0.3
