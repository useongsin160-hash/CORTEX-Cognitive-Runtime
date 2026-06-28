"""RPE reward sources.

Phase 6 STEP 1 ships two implementations:
- MockRewardSource: deterministic test signal.
- HeuristicOutcomeSource: weak observational signal.

CP3 and user_feedback sources are NOT implemented in STEP 1. They exist
only as values in RPESignalSource enum, reserved for later phases.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from app.rpe.models import RPEContext, RPEReward


@runtime_checkable
class RewardSourceProtocol(Protocol):
    async def compute_reward(self, context: RPEContext) -> RPEReward: ...


class MockRewardSource:
    """Deterministic reward source for tests and observe-only DI."""

    def __init__(
        self,
        reward_map: Mapping[str, tuple[float, float]] | None = None,
        default: tuple[float, float] = (0.5, 0.5),
    ) -> None:
        self._reward_map = dict(reward_map or {})
        self._default = default

    async def compute_reward(self, context: RPEContext) -> RPEReward:
        expected, actual = self._reward_map.get(context.trace_id, self._default)
        return RPEReward(
            source="mock",
            expected_reward=expected,
            actual_reward=actual,
            confidence=1.0,
        )


_NEGATIVE_RESPONSE_SOURCES = frozenset({"fallback"})
# cache hit and Tier-1.5 hit do NOT imply correctness in STEP 1.
_NEUTRAL_RESPONSE_SOURCES = frozenset({"exact_cache", "semantic_cache", "tier_1_5"})


class HeuristicOutcomeSource:
    """Observational signal over the pipeline outcome (B13).

    Encoded policy:
        - Negative: error / timeout / fallback (unchanged).
        - Positive (restored): observable process-quality signals — clean
          pipeline stages (planner/generator/context "ok"), a clean generation
          finish, and the relevance of the context actually used. Independent
          goods stack; a well-grounded clean success crosses the active gate
          while a partial/weak one stays sub-gate (no indiscriminate praise).
          The legacy "expected_*" labels remain a weak positive.
        - Confidence reflects objective corroboration — a clearly good OR clearly
          bad outcome is confident, an ambiguous one is not — and is BOUNDED at
          0.6 (process success != verified correctness; ground-truth confidence
          is reserved for CP3 / user_feedback).
    """

    def __init__(
        self,
        latency_threshold_ms: float = 100.0,
        baseline_expected: float = 0.5,
    ) -> None:
        if latency_threshold_ms <= 0:
            raise ValueError(
                f"latency_threshold_ms must be > 0, got {latency_threshold_ms}"
            )
        if not 0.0 <= baseline_expected <= 1.0:
            raise ValueError(
                f"baseline_expected must be in [0.0, 1.0], got {baseline_expected}"
            )
        self._latency_threshold_ms = latency_threshold_ms
        self._baseline_expected = baseline_expected

    async def compute_reward(self, context: RPEContext) -> RPEReward:
        actual = 0.5
        has_expected_label = False

        # ── negative evidence (B13: unchanged — demote already worked) ──
        if context.error_occurred:
            actual -= 0.25
        if context.timeout_occurred:
            actual -= 0.25
        if context.response_source in _NEGATIVE_RESPONSE_SOURCES:
            actual -= 0.20

        # ── existing positive evidence (kept; the expected_* label path is the
        # backward-compatible weak signal) ──
        if (
            0 < context.latency_ms < self._latency_threshold_ms
            and not context.error_occurred
            and not context.timeout_occurred
        ):
            actual += 0.05

        extra = context.extra_dict()
        if extra.get("expected_behavior_matched") is True:
            actual += 0.10
            has_expected_label = True
        if (
            extra.get("expected_continuation") is True
            and context.continuation_bypass
            and context.pfc_hint_applied
        ):
            actual += 0.05
            has_expected_label = True

        # ── B13: restored SUCCESS evidence (observable process quality) ──
        # A clean pipeline stage (not fallback) — each a small independent good
        # so partial success does not on its own cross the gate.
        if context.planner_ok:
            actual += 0.04
        if context.generator_ok:
            actual += 0.04
        if context.context_ok:
            actual += 0.04
        # A clean generation finish (stop, no fallback candidate).
        if context.clean_finish:
            actual += 0.08
        # Relevance of the context actually used — the differentiator that lifts
        # a well-grounded success past the gate; scaled by mean similarity
        # (cosine, mean-centred), capped. A weak/no-context run stays sub-gate.
        relevance_frac = max(0.0, min(1.0, context.context_mean_similarity / 0.5))
        actual += 0.16 * relevance_frac

        # ── confidence: restored from objective corroboration, bounded ──
        # delta = PE * confidence * max_delta; production previously stalled here
        # because confidence was 0.5 ONLY with an expected_* label, and the
        # pipeline never sets one → confidence stuck at 0.3 < the 0.5 gate, so
        # nothing ever learned. A clearly good OR clearly bad outcome corroborates
        # a confident judgement; an ambiguous/partial one does not.
        confidence = 0.3
        if context.planner_ok and context.generator_ok and context.context_ok:
            confidence += 0.15
        if context.clean_finish:
            confidence += 0.10
        if context.context_mean_similarity >= 0.3:
            confidence += 0.05
        if context.error_occurred:
            confidence += 0.15
        if context.timeout_occurred:
            confidence += 0.15
        if context.response_source in _NEGATIVE_RESPONSE_SOURCES:
            confidence += 0.10
        if has_expected_label:
            confidence += 0.20  # explicit label (backward-compat: label alone → 0.5)
        # ⚠️ cap: process success != verified correctness (a cleanly wrong answer
        # is possible). >0.6 is reserved for ground-truth sources (CP3 /
        # user_feedback), keeping observational learning bounded.
        confidence = min(0.6, confidence)

        if actual < 0.0:
            actual = 0.0
        elif actual > 1.0:
            actual = 1.0

        return RPEReward(
            source="heuristic",
            expected_reward=self._baseline_expected,
            actual_reward=actual,
            confidence=confidence,
        )
