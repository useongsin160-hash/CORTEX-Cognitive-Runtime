#!/usr/bin/env python3
"""B6 — 35-cell RPE learning trajectory harness (faithful + latent 2-pass).

Produces the data C (gate decision) consumes. STRICTLY a measurement tool:
- Fully isolated & deterministic: synthetic outcome archetypes + in-memory
  production components. No app.main / app.state, no live LLM, no network, no
  e5/chromadb. Same inputs → same outputs (except generated_at).
- Production behaviour change: 0. Every learning/routing component is the REAL
  production class (difficulty_store / calculators / mutators / service /
  route_override / routing_ratchet / routing_decay / skip_router / sources),
  wired here exactly as main.py wires them and run over a throwaway store. It
  never touches production wiring.

Why a rewrite (vs the old 7-cell t=0 single-shot): after B11 the learning axis
is category×difficulty (35 cells) with biological routing (override), a session
no-demote ratchet (S4), and step-based decay (S5). The old harness measured the
frozen 7-cell category-only path once per scenario and saw none of this. This
harness accumulates an N-step trajectory per (session, category, difficulty)
cell and measures the routing band, ratchet floor, and decay release.

Two passes — different reward SOURCE, same production gate (|PE|>=0.3, conf>=0.5)
and same manipulation boundary:

  faithful — production effective reward source [MockRewardSource(),
    HeuristicOutcomeSource()] (main.py). After B13 (reward restoration) the
    heuristic reads observable success signals, so a clean well-grounded success
    now crosses the gate (promote) and a clear failure demotes — this pass proves
    B13 end to end through the REAL components. (Before B13 this pass was inert:
    the heuristic could observe only failure, positive PE capped at +0.20 < the
    0.30 gate, and confidence never reached 0.5, so 35-cell learning never ran.)

  latent — a HARNESS-ONLY outcome→reward transfer source that weights the SAME
    observable signals more strongly (upper-bound mechanism probe), so a promote
    crosses faster and more decisively. This is the MECHANISM evidence: "does the
    biological routing differentiate correctly when fed strong gate-clearing
    reward". ⚠️ This transfer source is NOT production HeuristicOutcomeSource;
    "routing does X here" is a stronger-reward bound, not a claim that production
    produces such reward — see notes.latent_caveat.

Manipulation boundary (both passes):
- No seed manipulation: every cell starts from the mutator's neutral seed (0.3).
- No label-based reward: reward is computed from observable synthetic outcomes
  (planner/generator/context stage status / clean finish / context relevance /
  error / timeout) by the reward source's own policy. Reward NEVER reads
  context.difficulty. Outcome
  archetypes are assigned per CATEGORY (difficulty-neutral), never as a
  difficulty-monotonic function — the assignment is a measurement probe, NOT a
  claim that any category is "better".
- Neutrality assertion (the boundary made operational): two cells that differ
  ONLY in difficulty (same category, hence same archetype + same outcome
  sequence) MUST produce a bit-identical weight trajectory. neutrality_checks
  asserts this. Identical ⟹ difficulty selected only the cell address, never the
  delta (emergent invariant) ⟹ the 35-cell differentiation is the system's, not
  the harness's.

BG observations are RAW: bg_recommended (candidate_type) and routing_chose (path)
are recorded side by side as raw strings. NO agreement rate / candidate_type<->
path mapping is emitted — that semantic decision belongs to C (B6 measures, C
interprets).

Output: docs/measurements/three_mode_ablation.json + .md
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.schemas.context import CATEGORIES, Difficulty, TaskContext  # noqa: E402
from app.basal_ganglia.advisor import BasalGangliaAdvisor  # noqa: E402
from app.basal_ganglia.models import ActionSelectionContext  # noqa: E402
from app.core.logging import SpinalLogger  # noqa: E402
from app.routing.routing_decay import RoutingDecay, _baseline_band  # noqa: E402
from app.routing.routing_ratchet import RoutingRatchet  # noqa: E402
from app.routing.rpe_route_override import DifficultyRouteOverride  # noqa: E402
from app.routing.skip_router import SkipLogicRouter  # noqa: E402
from app.rpe.calculators import SynapseDifficultyDryRunCalculator  # noqa: E402
from app.rpe.difficulty_learner import RPEDifficultyLearner  # noqa: E402
from app.rpe.difficulty_store import InMemorySynapseDifficultyWeightStore  # noqa: E402
from app.rpe.dopamine import DopamineRPE  # noqa: E402
from app.rpe.models import ActiveMutationConfig, RPEContext, RPEReward  # noqa: E402
from app.rpe.mutators import SynapseDifficultyWeightMutator  # noqa: E402
from app.rpe.service import RPEMutationService  # noqa: E402
from app.rpe.sources import HeuristicOutcomeSource, MockRewardSource  # noqa: E402

# Measurement parameters (HARNESS values, NOT production). The active gate stays
# at the production values below — never relaxed here.
N_STEPS = 30          # trajectory length: both passes' clean cell saturates; partial stays sub-gate
RISE_STEPS = 15       # rise phase: latent cell climbs seed 0.3 -> promote (>=0.7)
IDLE_STEPS = 60       # idle gap: decay -0.60 carries a promoted cell (~0.9) below the 0.4 release threshold
_SEED_WEIGHT = 0.3    # SynapseDifficultyWeightMutator read-seed (unlearned cell origin)
_MIN_CONFIDENCE = 0.5         # ActiveMutationConfig default (production gate)
_MIN_ABS_PREDICTION_ERROR = 0.3  # ActiveMutationConfig default (production gate)

_DIFFICULTIES: tuple[Difficulty, ...] = tuple(Difficulty)  # 1..5 (full B12 range)

# Outcome archetype per category — difficulty-NEUTRAL (every difficulty of a
# category shares it). A deliberate spread (up / down / mixed / flat) so the
# trajectory shows differentiation; the assignment is an arbitrary probe and
# carries NO "this category is better" meaning.
_ARCHETYPE_BY_CATEGORY: dict[str, str] = {
    "coding": "clean",
    "game_design": "clean",
    "math_logic": "failing",
    "writing": "degrading",
    "data_analysis": "failing",
    "system_design": "degrading",
    "general": "neutral",
}


# ---------------------------------------------------------------------------
# Latent-pass reward source (HARNESS-ONLY — never imported by app/)
# ---------------------------------------------------------------------------
class OutcomeTransferRewardSource:
    """Outcome→reward transfer with a range wide enough to clear the production
    active gate (|PE|>=0.3). Reads ONLY observable outcome fields — NEVER
    context.difficulty (label-neutral; neutrality_checks asserts it). Distinct
    from production HeuristicOutcomeSource (whose positive PE caps at +0.20); see
    notes.latent_caveat. Used by the latent pass only.
    """

    BASELINE = 0.5
    CONFIDENCE = 0.8

    async def compute_reward(self, context: RPEContext) -> RPEReward:
        actual = self.BASELINE
        # positive evidence — the SAME observable success signals production reads
        # (B13), weighted more strongly (upper-bound probe, not production fidelity).
        if context.planner_ok:
            actual += 0.10
        if context.generator_ok:
            actual += 0.10
        if context.context_ok:
            actual += 0.10
        if context.clean_finish:
            actual += 0.10
        actual += 0.20 * max(0.0, min(1.0, context.context_mean_similarity / 0.5))
        # negative evidence
        if context.error_occurred:
            actual -= 0.30
        if context.timeout_occurred:
            actual -= 0.30
        if context.response_source == "fallback":
            actual -= 0.20
        actual = min(1.0, max(0.0, actual))
        return RPEReward(
            source="heuristic",  # RPESignalSource literal; semantically harness transfer
            expected_reward=self.BASELINE,
            actual_reward=actual,
            confidence=self.CONFIDENCE,
        )


def _faithful_sources() -> list:
    return [MockRewardSource(), HeuristicOutcomeSource()]


def _latent_sources() -> list:
    return [OutcomeTransferRewardSource()]


_PASSES = (("faithful", _faithful_sources), ("latent", _latent_sources))


# ---------------------------------------------------------------------------
# Outcome archetypes (difficulty-neutral synthetic outcome sequences)
# ---------------------------------------------------------------------------
def _archetype_outcome(name: str, step: int, total: int) -> dict:
    """Return the observable outcome record for a given archetype step.

    Post-B13 the reward is driven by observable process signals (no expected_*
    labels — production never sets them):
    clean    — all stages ok, clean finish, relevant context → promote.
    failing  — error+timeout+fallback, stages not ok → demote.
    degrading— clean for the first half, failing for the second.
    neutral  — pipeline ran (planner/generator ok) but no grounded context / no
               clean finish → sub-gate, stays flat in both passes.
    """
    if name == "clean":
        return {
            "error_occurred": False,
            "timeout_occurred": False,
            "response_source": "swarm",
            "latency_ms": 50.0,
            "planner_ok": True,
            "generator_ok": True,
            "context_ok": True,
            "clean_finish": True,
            "context_mean_similarity": 0.6,
        }
    if name == "failing":
        return {
            "error_occurred": True,
            "timeout_occurred": True,
            "response_source": "fallback",
            "latency_ms": 250.0,
            "planner_ok": False,
            "generator_ok": False,
            "context_ok": False,
            "clean_finish": False,
            "context_mean_similarity": 0.0,
        }
    if name == "degrading":
        if step * 2 <= total:
            return _archetype_outcome("clean", step, total)
        return _archetype_outcome("failing", step, total)
    # neutral
    return {
        "error_occurred": False,
        "timeout_occurred": False,
        "response_source": "swarm",
        "latency_ms": 250.0,
        "planner_ok": True,
        "generator_ok": True,
        "context_ok": False,
        "clean_finish": False,
        "context_mean_similarity": 0.0,
    }


def _outcome_tag(outcome: dict) -> str:
    if outcome["error_occurred"] or outcome["timeout_occurred"]:
        return "neg"
    if outcome.get("clean_finish") and outcome.get("context_ok"):
        return "pos"
    return "flat"


def _rpe_context(
    session: str, category: str, difficulty: int, step: int, outcome: dict
) -> RPEContext:
    extra: list[tuple[str, bool]] = []
    if outcome.get("expected_behavior_matched"):
        extra.append(("expected_behavior_matched", True))
    if outcome.get("expected_continuation"):
        extra.append(("expected_continuation", True))
    return RPEContext(
        trace_id=f"{session}:step{step}",
        session_id=session,
        category=category,
        difficulty=difficulty,
        response_source=outcome["response_source"],
        latency_ms=outcome["latency_ms"],
        error_occurred=outcome["error_occurred"],
        timeout_occurred=outcome["timeout_occurred"],
        continuation_bypass=outcome.get("continuation_bypass", False),
        pfc_hint_applied=outcome.get("pfc_hint_applied", False),
        planner_ok=outcome.get("planner_ok", False),
        generator_ok=outcome.get("generator_ok", False),
        context_ok=outcome.get("context_ok", False),
        clean_finish=outcome.get("clean_finish", False),
        context_mean_similarity=outcome.get("context_mean_similarity", 0.0),
        extra=tuple(extra),
    )


def _task_context(session: str, category: str, difficulty: int, step: int) -> TaskContext:
    return TaskContext(
        trace_id=f"{session}:step{step}",
        category=category,
        difficulty=Difficulty(difficulty),
    )


def _round(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# Production component wiring (mirrors main.py:244-286), per isolated bundle
# ---------------------------------------------------------------------------
@dataclass
class _Wiring:
    store: InMemorySynapseDifficultyWeightStore
    service: RPEMutationService
    dopamine: DopamineRPE
    calculator: SynapseDifficultyDryRunCalculator
    learner: RPEDifficultyLearner
    router: SkipLogicRouter
    override: DifficultyRouteOverride
    ratchet: RoutingRatchet
    decay: RoutingDecay


def _build_wiring(
    sources: list, *, active_enabled: bool, difficulty_learning_enabled: bool,
    logger: SpinalLogger,
) -> _Wiring:
    """Build an isolated, throwaway production wiring bundle (35-cell store +
    learner + biological routing). Same shape as main.py — the harness measures
    the real components, never a re-implementation."""
    store = InMemorySynapseDifficultyWeightStore()
    mutator = SynapseDifficultyWeightMutator(store=store)
    service = RPEMutationService(
        mutator=mutator,
        logger=logger,
        config=ActiveMutationConfig(
            active_enabled=active_enabled,
            difficulty_learning_enabled=difficulty_learning_enabled,
        ),
    )
    dopamine = DopamineRPE(sources=sources, logger=logger)
    calculator = SynapseDifficultyDryRunCalculator()
    learner = RPEDifficultyLearner(
        dopamine_rpe=dopamine, calculator=calculator, service=service, logger=logger
    )
    ratchet = RoutingRatchet(logger=logger)
    return _Wiring(
        store=store,
        service=service,
        dopamine=dopamine,
        calculator=calculator,
        learner=learner,
        router=SkipLogicRouter(),
        override=DifficultyRouteOverride(store=store, logger=logger),
        ratchet=ratchet,
        decay=RoutingDecay(store=store, ratchet=ratchet, logger=logger),
    )


async def _route_once(w: _Wiring, tctx: TaskContext, session: str) -> str:
    """One request's routing read (production routes.py order): decay.step (lazy
    idle realize) -> skip_router base -> override band shift -> ratchet floor
    clamp. Returns the final physical path label."""
    await w.decay.step(tctx, session)
    base = await w.router.route(tctx)
    overridden = await w.override.apply(base, tctx, session)
    final = await w.ratchet.apply(overridden, tctx, session)
    return final.path


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------
async def _measure_trajectory(
    pass_name: str, sources: list, logger: SpinalLogger
) -> list[dict]:
    """Per-cell N-step weight trajectory under the active learner (35 cells)."""
    primary = sources[-1]  # the outcome-derived source (heuristic / transfer)
    cells: list[dict] = []
    for category in CATEGORIES:
        archetype = _ARCHETYPE_BY_CATEGORY[category]
        for difficulty in _DIFFICULTIES:
            d = int(difficulty)
            w = _build_wiring(
                sources, active_enabled=True, difficulty_learning_enabled=True,
                logger=logger,
            )
            session = f"{pass_name}:{category}:{d}"
            trajectory: list[dict] = []
            for step in range(1, N_STEPS + 1):
                outcome = _archetype_outcome(archetype, step, N_STEPS)
                ctx = _rpe_context(session, category, d, step, outcome)
                reward = await primary.compute_reward(ctx)  # PE actually driving learning
                await w.learner.learn(ctx)
                weight = await w.store.read_weight(session, category, d)
                trajectory.append({
                    "step": step,
                    "trace_id": ctx.trace_id,
                    "outcome": _outcome_tag(outcome),
                    "prediction_error": _round(reward.prediction_error),
                    "weight": _round(weight) if weight is not None else None,
                })
            cells.append({
                "pass": pass_name,
                "session": session,
                "category": category,
                "difficulty": d,
                "archetype": archetype,
                "baseline_band": _baseline_band(d),
                "seed_weight": _SEED_WEIGHT,
                "trajectory": trajectory,
            })
    return cells


# Representative cells for 3-mode isolation: a promotable low-difficulty cell and
# a high-difficulty (baseline full_pipeline) cell, both in a 'clean' category.
_ISO_CELLS: tuple[tuple[str, int], ...] = (("coding", 2), ("coding", 5))


async def _measure_mode_isolation(
    pass_name: str, sources: list, logger: SpinalLogger
) -> list[dict]:
    """Same trajectory run under observe / dry_run / active; record the route
    path per step. observe & dry_run must leave the path at the B12-native
    baseline (weight never changes); only active moves it."""
    out: list[dict] = []
    for category, difficulty in _ISO_CELLS:
        archetype = _ARCHETYPE_BY_CATEGORY[category]
        for mode in ("observe", "dry_run", "active"):
            is_active = mode == "active"
            w = _build_wiring(
                sources, active_enabled=is_active,
                difficulty_learning_enabled=is_active, logger=logger,
            )
            session = f"{pass_name}:iso:{category}:{difficulty}:{mode}"
            paths: list[str] = []
            for step in range(1, N_STEPS + 1):
                outcome = _archetype_outcome(archetype, step, N_STEPS)
                ctx = _rpe_context(session, category, difficulty, step, outcome)
                tctx = _task_context(session, category, difficulty, step)
                paths.append(await _route_once(w, tctx, session))
                # post-response learning per mode (production gating semantics)
                if mode == "dry_run":
                    # compute proposals, never apply (store stays empty)
                    for decision in await w.dopamine.observe(ctx):
                        w.calculator.compute_proposal(decision, current_value=None)
                else:
                    # observe: difficulty_learning_enabled=False → learn() no-ops.
                    # active: full learner applies.
                    await w.learner.learn(ctx)
            out.append({
                "pass": pass_name,
                "cell": f"{category}:{difficulty}",
                "mode": mode,
                "baseline_band": _baseline_band(difficulty),
                "route_path_per_step": paths,
                "distinct_paths": sorted(set(paths)),
            })
    return out


# Ratchet/decay survival contrasts: (a) low-difficulty promote cell whose floor
# is released by decay (demote restored), (b) high-difficulty cell whose
# B12-native baseline floor is exempt (protected).
_RD_CELLS: tuple[tuple[str, int, str], ...] = (
    ("coding", 2, "low_diff_promote_release"),
    ("coding", 5, "high_diff_baseline_protected"),
)
_RD_IDLE_CELL: tuple[str, int] = ("game_design", 2)  # different cell to advance idle steps


async def _measure_ratchet_decay(
    pass_name: str, sources: list, logger: SpinalLogger
) -> list[dict]:
    """Rise the cell (promote + ratchet lock), idle it (advance another cell),
    then revisit so decay realizes the idle gap and releases the floor."""
    out: list[dict] = []
    for category, difficulty, label in _RD_CELLS:
        archetype = _ARCHETYPE_BY_CATEGORY[category]
        idle_archetype = _ARCHETYPE_BY_CATEGORY[_RD_IDLE_CELL[0]]
        w = _build_wiring(
            sources, active_enabled=True, difficulty_learning_enabled=True, logger=logger
        )
        session = f"{pass_name}:rd:{category}:{difficulty}"

        # rise phase — promote + ratchet lock
        for step in range(1, RISE_STEPS + 1):
            outcome = _archetype_outcome(archetype, step, RISE_STEPS)
            ctx = _rpe_context(session, category, difficulty, step, outcome)
            tctx = _task_context(session, category, difficulty, step)
            await _route_once(w, tctx, session)
            await w.learner.learn(ctx)
        weight_after_rise = await w.store.read_weight(session, category, difficulty)
        floor_after_rise = w.ratchet._floors.get(session, {}).get((category, difficulty))

        # idle phase — advance another cell (target left untouched)
        for step in range(RISE_STEPS + 1, RISE_STEPS + 1 + IDLE_STEPS):
            ic, idiff = _RD_IDLE_CELL
            outcome = _archetype_outcome(idle_archetype, step, IDLE_STEPS)
            ctx = _rpe_context(session, ic, idiff, step, outcome)
            tctx = _task_context(session, ic, idiff, step)
            await _route_once(w, tctx, session)
            await w.learner.learn(ctx)

        # revisit — decay.step realizes the accrued idle decay (lazy) + release
        revisit_step = RISE_STEPS + IDLE_STEPS + 1
        tctx = _task_context(session, category, difficulty, revisit_step)
        await w.decay.step(tctx, session)
        decayed_weight = await w.store.read_weight(session, category, difficulty)
        floor_after_idle = w.ratchet._floors.get(session, {}).get((category, difficulty))

        out.append({
            "pass": pass_name,
            "cell": f"{category}:{difficulty}",
            "label": label,
            "baseline_band": _baseline_band(difficulty),
            "weight_after_rise": _round(weight_after_rise) if weight_after_rise is not None else None,
            "floor_after_rise": floor_after_rise,
            "idle_events": [{
                "at_step": revisit_step,
                "idle_steps": IDLE_STEPS,
                "decayed_weight": _round(decayed_weight) if decayed_weight is not None else None,
                "released_floor": floor_after_idle,
            }],
            "released_floor": floor_after_idle,
        })
    return out


def _neutrality_checks(cells: list[dict]) -> list[dict]:
    """Within each (pass, category): all difficulties share the same archetype +
    outcome sequence, so their weight trajectories MUST be bit-identical. Identical
    ⟹ difficulty never entered the delta (the manipulation boundary holds)."""
    out: list[dict] = []
    groups: dict[tuple[str, str], list[dict]] = {}
    for c in cells:
        groups.setdefault((c["pass"], c["category"]), []).append(c)
    for (pass_name, category), group in groups.items():
        group = sorted(group, key=lambda c: c["difficulty"])
        seqs = [tuple(p["weight"] for p in c["trajectory"]) for c in group]
        identical = all(seq == seqs[0] for seq in seqs)
        out.append({
            "pass": pass_name,
            "category": category,
            "archetype": group[0]["archetype"],
            "difficulties_compared": [c["difficulty"] for c in group],
            "identical_trajectory": identical,
        })
    return out


async def _measure_bg_observations(logger: SpinalLogger) -> list[dict]:
    """RAW side-channel: BG recommendation vs B12-native routing path, side by
    side. NO agreement computed — candidate_type<->path mapping is C's call. BG
    features are a deterministic function of difficulty (this is NOT the reward
    path; the manipulation boundary governs reward/learning, not raw BG)."""
    advisor = BasalGangliaAdvisor(logger=logger)
    router = SkipLogicRouter()
    out: list[dict] = []
    for category in CATEGORIES:
        archetype = _ARCHETYPE_BY_CATEGORY[category]
        for difficulty in _DIFFICULTIES:
            d = int(difficulty)
            bg_ctx = ActionSelectionContext(
                trace_id=f"bg:{category}:{d}",
                session_id=f"bg:{category}:{d}",
                category=category,
                difficulty=d,
                pfc_active=d >= 3,
                pfc_cue_type=None,
                pfc_confidence=min(0.9, 0.2 * d),
                pfc_intent_category=None,
                lc_ne_level=min(0.9, 0.2 * d),
                lc_intent_label=None,
                synapse_weights=((category, 0.5),),
                rpe_recent_positive_count=0,
                rpe_recent_negative_count=0,
            )
            decision = await advisor.evaluate(bg_ctx)
            bg_recommended = decision.selected.candidate_type if decision.selected else None
            route = await router.route(
                TaskContext(trace_id=f"bg:{category}:{d}", category=category, difficulty=Difficulty(d))
            )
            out.append({
                "category": category,
                "difficulty": d,
                "archetype": archetype,
                "bg_recommended": bg_recommended,   # candidate_type (raw)
                "routing_chose": route.path,         # B12-native path (raw)
            })
    return out


async def _collect() -> dict:
    logger = SpinalLogger()
    cells: list[dict] = []
    mode_isolation: list[dict] = []
    ratchet_decay: list[dict] = []
    for pass_name, src_factory in _PASSES:
        sources = src_factory()
        cells += await _measure_trajectory(pass_name, sources, logger)
        mode_isolation += await _measure_mode_isolation(pass_name, sources, logger)
        ratchet_decay += await _measure_ratchet_decay(pass_name, sources, logger)
    return {
        "cells": cells,
        "mode_isolation": mode_isolation,
        "ratchet_decay": ratchet_decay,
        "neutrality_checks": _neutrality_checks(cells),
        "bg_observations": await _measure_bg_observations(logger),
    }


def run_measurement() -> dict:
    """Pure entry point — returns the report dict (writes nothing). Deterministic
    (no uuid / wall-clock leaks into the report besides generated_at)."""
    data = asyncio.run(_collect())
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness": "measure_3mode_ablation",
        "deterministic": True,
        "production_behavior_change": 0,
        "grid": {
            "categories": list(CATEGORIES),
            "difficulties": [int(d) for d in _DIFFICULTIES],
            "archetype_by_category": dict(_ARCHETYPE_BY_CATEGORY),
            "params": {
                "n_steps": N_STEPS,
                "rise_steps": RISE_STEPS,
                "idle_steps": IDLE_STEPS,
                "seed_weight": _SEED_WEIGHT,
                "min_confidence": _MIN_CONFIDENCE,
                "min_abs_prediction_error": _MIN_ABS_PREDICTION_ERROR,
            },
        },
        "passes": [name for name, _ in _PASSES],
        "cells": data["cells"],
        "mode_isolation": data["mode_isolation"],
        "ratchet_decay": data["ratchet_decay"],
        "neutrality_checks": data["neutrality_checks"],
        "bg_observations": data["bg_observations"],
        "notes": {
            "passes": (
                "faithful = production effective reward source [Mock + Heuristic] "
                "(main.py). After B13 the heuristic reads observable success signals, "
                "so a clean well-grounded success crosses the gate (promote) and a "
                "clear failure demotes — this pass proves B13 through the real "
                "components. latent = harness-only transfer source weighting the SAME "
                "signals more strongly (upper-bound mechanism probe). Both keep the "
                "production gate (conf>=0.5, |PE|>=0.3)."
            ),
            "latent_caveat": (
                "The latent transfer source is NOT production HeuristicOutcomeSource — "
                "it weights the same observable signals more strongly. 'Routing does X "
                "here' is a stronger-reward upper bound, not a production-fidelity "
                "claim; the faithful pass is the production-calibrated result."
            ),
            "manipulation_boundary": (
                "No seed manipulation (all cells start at seed 0.3). No label-based "
                "reward — reward is computed from observable synthetic outcomes by the "
                "source policy and NEVER reads difficulty. Archetypes are assigned per "
                "category (difficulty-neutral), an arbitrary probe with no 'category is "
                "better' meaning. neutrality_checks asserts that cells differing only in "
                "difficulty have bit-identical trajectories (difficulty selects the cell "
                "address, never the delta)."
            ),
            "bg": (
                "RAW ONLY. bg_recommended (candidate_type) and routing_chose (path) are "
                "recorded side by side as raw strings. B6 does NOT compute an agreement "
                "rate or any candidate_type<->path mapping — that belongs to C."
            ),
            "isolation": (
                "Every learning/routing component is the real production class run over a "
                "throwaway in-memory store. No app.main / app.state, no live LLM / network "
                "/ e5. Production behaviour change: 0."
            ),
            "determinism": (
                "Fully synthetic: fixed archetypes + deterministic outcome sequences. Same "
                "inputs → same outputs (except generated_at)."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def _final_weight(cell: dict):
    for p in reversed(cell["trajectory"]):
        if p["weight"] is not None:
            return p["weight"]
    return None


def _to_markdown(report: dict) -> str:
    g = report["grid"]
    lines = [
        "# RPE 35-Cell Learning Trajectory Measurement (B6)",
        "",
        f"Generated: {report['generated_at']}",
        f"Grid: {len(g['categories'])} categories x {len(g['difficulties'])} "
        f"difficulties (1-5) x {len(report['passes'])} passes "
        f"({', '.join(report['passes'])})",
        f"Params: N_STEPS={g['params']['n_steps']}, RISE={g['params']['rise_steps']}, "
        f"IDLE={g['params']['idle_steps']}, seed={g['params']['seed_weight']}, "
        f"gate conf>={g['params']['min_confidence']} |PE|>={g['params']['min_abs_prediction_error']}",
        "",
        "> Measurement only. faithful = production reward source (shows current promote "
        "inertness). latent = harness transfer source (exercises the mechanism — NOT a "
        "production-fidelity claim). BG observations are RAW (no agreement rate).",
        "",
        "## Final cell weight (seed 0.3) — per pass",
        "",
        "| Pass | Category | Archetype | d1 | d2 | d3 | d4 | d5 |",
        "|------|----------|-----------|----|----|----|----|----|",
    ]
    by_pc: dict[tuple[str, str], dict[int, dict]] = {}
    for c in report["cells"]:
        by_pc.setdefault((c["pass"], c["category"]), {})[c["difficulty"]] = c
    for (pass_name, category), per_diff in by_pc.items():
        archetype = next(iter(per_diff.values()))["archetype"]
        cols = []
        for d in (1, 2, 3, 4, 5):
            cell = per_diff.get(d)
            fw = _final_weight(cell) if cell else None
            cols.append("seed" if fw is None else f"{fw:.3f}")
        lines.append(
            f"| {pass_name} | {category} | {archetype} | " + " | ".join(cols) + " |"
        )

    lines += [
        "",
        "## Neutrality checks (difficulty-label boundary proof)",
        "",
        "| Pass | Category | Archetype | difficulties | identical trajectory |",
        "|------|----------|-----------|--------------|----------------------|",
    ]
    for n in report["neutrality_checks"]:
        lines.append(
            f"| {n['pass']} | {n['category']} | {n['archetype']} | "
            f"{n['difficulties_compared']} | {n['identical_trajectory']} |"
        )

    lines += [
        "",
        "## 3-mode isolation (route path)",
        "",
        "| Pass | Cell | Mode | baseline | distinct paths |",
        "|------|------|------|----------|----------------|",
    ]
    for m in report["mode_isolation"]:
        lines.append(
            f"| {m['pass']} | {m['cell']} | {m['mode']} | {m['baseline_band']} | "
            f"{m['distinct_paths']} |"
        )

    lines += [
        "",
        "## Ratchet / decay survival",
        "",
        "| Pass | Cell | label | weight after rise | floor after rise | "
        "decayed weight | released floor |",
        "|------|------|-------|-------------------|------------------|"
        "----------------|----------------|",
    ]
    for r in report["ratchet_decay"]:
        ev = r["idle_events"][0]
        lines.append(
            f"| {r['pass']} | {r['cell']} | {r['label']} | {r['weight_after_rise']} | "
            f"{r['floor_after_rise']} | {ev['decayed_weight']} | {r['released_floor']} |"
        )

    lines += [
        "",
        "## BasalGanglia raw observations (bg_recommended vs routing_chose)",
        "",
        "| Category | difficulty | bg_recommended | routing_chose |",
        "|----------|------------|----------------|---------------|",
    ]
    for o in report["bg_observations"]:
        lines.append(
            f"| {o['category']} | {o['difficulty']} | {o['bg_recommended']} | "
            f"{o['routing_chose']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    out_dir = ROOT / "docs" / "measurements"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_measurement()

    json_path = out_dir / "three_mode_ablation.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    md_path = out_dir / "three_mode_ablation.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_to_markdown(report) + "\n")

    n_cells = len(report["cells"])
    print(f"35-cell trajectory: {n_cells} cells across {len(report['passes'])} passes")
    print(f"Reports written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
