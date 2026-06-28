"""BasalGanglia action selection policy (compute-demand matching).

Phase 6 STEP 5.1; redesigned (C2 prerequisite).

ActionSelectionPolicy.select() scores candidates and produces a recommendation.
Deterministic tie-breaker:
    1. score desc
    2. candidate_type priority: swarm_full > swarm_minimal > tier_1_5_augment > fallback
    3. candidate_id lex asc

Scoring (redesign):
    Each candidate_type sits on a compute ladder — its compute *level* L:
        swarm_full 1.0 > swarm_minimal 0.667 > tier_1_5_augment 0.333 > fallback 0.0
    The context yields a single compute *demand* D in [0, 1]: an anchor at the
    difficulty's B12 routing band (1→1/3, 2·3→2/3, 4·5→1.0 — the system's own
    difficulty→band policy, so BG's baseline AGREES with routing) MODULATED by the
    other REAL signals as signed deviations centered on their neutral point:
        + ne_factor      * ne                    (NE present → escalate)
        + rpe_factor     * (2*neg_frac - 1)      (recent failures → escalate)
        - synapse_factor * (2*synapse - 1)       (familiar category → de-escalate)
        - pfc_factor     * (2*pfc_confidence - 1)(confident cue → de-escalate)
    Neutral signals (ne 0, balanced RPE, synapse/pfc 0.5) leave D at the baseline;
    a missing signal contributes nothing (no fabricated value). score = 1 - |L - D|
    clamped to [0, 1] — the closest compute level wins. Because the anchor maps 4·5
    onto full_pipeline's level (1.0), a hard query stays at swarm_full unless the
    modulators (kept below the band width) genuinely lower it; a well-known,
    confident, low-difficulty query can fall to a lighter candidate (legitimate
    lightening). A residual high-difficulty demotion is further blocked downstream
    by the no-demote ratchet floor at apply time. The old additive form let only
    the LC caution bonus differentiate, and it favored the lightest candidate at
    high difficulty — the wrong direction.

Why the rewrite: the four default candidates carry identical synapse/pfc/rpe
snapshots, so in the old additive sum those terms were common-mode and could never
break a tie; only lc_caution_bonus (defensive types, NE>=0.5) moved the argmax —
which demoted difficulty 4·5 to swarm_minimal. Demand-matching makes every signal
differentiate the candidates and points escalation the right way.

No mutation of inputs. No live object references. No production side-effects.
"""
from __future__ import annotations

from app.basal_ganglia.models import (
    ActionCandidate,
    ActionSelectionContext,
    ActionSelectionPolicyConfig,
)

# Priority for deterministic tie-breaker. Lower number = higher priority.
_TYPE_PRIORITY: dict[str, int] = {
    "swarm_full": 0,
    "swarm_minimal": 1,
    "tier_1_5_augment": 2,
    "fallback": 3,
}

# Compute level per candidate_type on the demand ladder (mirrors the tie-breaker
# priority order). Higher = heavier compute.
_TYPE_COMPUTE_LEVEL: dict[str, float] = {
    "swarm_full": 1.0,
    "swarm_minimal": 2.0 / 3.0,
    "tier_1_5_augment": 1.0 / 3.0,
    "fallback": 0.0,
}

# difficulty → demand anchor, aligned to the B12 skip-router band policy
# ({1}=lightweight, {2,3}=standard, {4,5}=full_pipeline) projected onto the
# compute-level ladder. This makes BG's difficulty baseline agree with routing
# rather than running one band lighter.
_LIGHTWEIGHT_LEVEL: float = 1.0 / 3.0
_STANDARD_LEVEL: float = 2.0 / 3.0
_FULL_LEVEL: float = 1.0


def _difficulty_demand(difficulty: int) -> float:
    """B12 band-anchored difficulty demand on the compute-level ladder."""
    if difficulty >= 4:
        return _FULL_LEVEL
    if difficulty >= 2:
        return _STANDARD_LEVEL
    return _LIGHTWEIGHT_LEVEL


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _context_synapse(context: ActionSelectionContext) -> float | None:
    """The synapse weight for the context category, or None if not present."""
    if context.category is None:
        return None
    for key, value in context.synapse_weights:
        if key == context.category:
            return float(value)
    return None


class ActionSelectionPolicy:
    """Score candidates by compute-demand match and pick one (recommendation only)."""

    def __init__(self, config: ActionSelectionPolicyConfig | None = None) -> None:
        self._config = config if config is not None else ActionSelectionPolicyConfig()

    @property
    def config(self) -> ActionSelectionPolicyConfig:
        return self._config

    def select(
        self,
        context: ActionSelectionContext,
        candidates: tuple[ActionCandidate, ...],
    ) -> tuple[ActionCandidate | None, float, str]:
        """Score candidates, pick a winner, return (selected, confidence, reason).

        Empty candidates → (None, 0.0, "no_candidates").
        """
        if len(candidates) == 0:
            return None, 0.0, "no_candidates"

        # Demand depends only on the context — compute it once, then match each
        # candidate's compute level against it.
        demand, primary = self._demand(context)

        scored: list[tuple[ActionCandidate, float]] = []
        for cand in candidates:
            level = _TYPE_COMPUTE_LEVEL.get(cand.candidate_type, 0.5)
            score = _clamp(1.0 - abs(level - demand), 0.0, 1.0)
            scored.append((cand, score))

        # Sort by:
        #   1. score desc (use -score for asc sort)
        #   2. candidate_type priority asc (swarm_full first)
        #   3. candidate_id lex asc
        scored.sort(
            key=lambda t: (
                -t[1],
                _TYPE_PRIORITY.get(t[0].candidate_type, 99),
                t[0].candidate_id,
            )
        )

        top_cand, top_score = scored[0]
        if len(scored) == 1:
            confidence = _clamp(top_score, 0.0, 1.0)
        else:
            second_score = scored[1][1]
            margin = top_score - second_score
            confidence = _clamp(top_score * 0.6 + margin * 0.4, 0.0, 1.0)

        reason = self._build_reason(top_cand, top_score, demand, primary)
        return top_cand, confidence, reason

    def _demand(self, context: ActionSelectionContext) -> tuple[float, str]:
        """Compute the context's compute demand D in [0, 1] + the primary term.

        D = difficulty-band anchor + signed modulations from the REAL signals
        present. Each modulation is centered on the signal's neutral point, so a
        missing or neutral signal shifts D by 0 (never fabricated). The primary
        label is the modulator with the largest absolute shift, or "difficulty"
        when the anchor dominates.
        """
        cfg = self._config
        anchor = _difficulty_demand(context.difficulty)

        # (label, signed deviation) for each present modulator.
        deltas: list[tuple[str, float]] = []

        if context.lc_ne_level is not None:
            # One-sided escalator: neutral at 0 (no NE), max at 1.
            deltas.append(
                ("ne", cfg.ne_demand_factor * _clamp(context.lc_ne_level, 0.0, 1.0))
            )

        total_rpe = (
            context.rpe_recent_positive_count + context.rpe_recent_negative_count
        )
        if total_rpe > 0:
            neg_frac = context.rpe_recent_negative_count / total_rpe
            # Centered: balanced (0.5) → 0; all-fail → +factor; all-success → -factor.
            deltas.append(("rpe", cfg.rpe_demand_factor * (2.0 * neg_frac - 1.0)))

        synapse = _context_synapse(context)
        if synapse is not None:
            # Familiar (synapse > 0.5) de-escalates; unfamiliar escalates.
            deltas.append(
                ("synapse", -cfg.synapse_demand_factor * (2.0 * _clamp(synapse, 0.0, 1.0) - 1.0))
            )

        if context.pfc_confidence is not None:
            # Confident (> 0.5) de-escalates; uncertain escalates.
            deltas.append(
                ("pfc", -cfg.pfc_demand_factor * (2.0 * _clamp(context.pfc_confidence, 0.0, 1.0) - 1.0))
            )

        demand = _clamp(anchor + sum(d for _, d in deltas), 0.0, 1.0)
        # Primary = the largest-magnitude modulator, else the difficulty anchor.
        if deltas:
            label, mag = max(((lbl, abs(d)) for lbl, d in deltas), key=lambda t: t[1])
            primary = label if mag > 0.0 else "difficulty"
        else:
            primary = "difficulty"
        return demand, primary

    def _build_reason(
        self,
        selected: ActionCandidate,
        top_score: float,
        demand: float,
        primary: str,
    ) -> str:
        level = _TYPE_COMPUTE_LEVEL.get(selected.candidate_type, 0.5)
        return (
            f"type={selected.candidate_type};"
            f"level={level:.3f};"
            f"demand={demand:.4f};"
            f"match={top_score:.4f};"
            f"primary={primary}"
        )
