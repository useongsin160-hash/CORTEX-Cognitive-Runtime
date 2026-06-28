# ADR-013: BasalGanglia Advisor (STEP 5.1)

## Status

Accepted (Phase 6 STEP 5.1)

## Context

Phase 6 STEPs 1–4 delivered the RPE stack:
- STEP 1: Observe-only data model
- STEP 2: Dry-run simulation
- STEP 3.1/3.2: Active synapse_weight mutation + pipeline integration
- STEP 4: IFOM TTL target extension

Phase 6 STEP 5 introduces the **BasalGanglia** layer — action selection.
Per the design plan, STEP 5 is split into two sub-steps:

- **STEP 5.1** (this ADR): BasalGanglia Advisor — read-only / recommendation-only.
- **STEP 5.2** (deferred to closeout): Conflict Resolution.

STEP 5.1 introduces no production behavior change. The advisor scores
action candidates from snapshots and emits a recommendation that is
**not** consumed by PFC, Planner, LC, Swarm, or routes.

## Decision

### Module location

New module `app/basal_ganglia/` with four files:
- `__init__.py` — public exports
- `models.py` — `ActionCandidate`, `ActionSelectionContext`,
  `ActionSelectionDecision`, `ActionSelectionPolicyConfig`
- `policies.py` — `ActionSelectionPolicy`
- `advisor.py` — `BasalGangliaAdvisor` + builder helper

PFC/LC/Swarm/routes/main **MUST NOT** import `app.basal_ganglia` in STEP 5.1.

### Read-only / recommendation-only invariant

`ActionSelectionDecision.applied` is hard-locked to `False` at validation time.
Constructing a decision with `applied=True` raises `ValueError`.

The advisor:
- **Reads** snapshot values (synapse weights, IFOM overrides, PFC/LC state).
- **Writes** nothing to stores, configs, or live objects.
- **Logs** `bg.evaluated` / `bg.error` events only.

### Snapshot-only inputs

`ActionSelectionContext` is a frozen dataclass. To prevent leakage from live
mappings into the decision graph, **no `dict` fields** are allowed. Mappings
are converted to `tuple[tuple[str, T], ...]` at construction time:

| Field | Type |
|-------|------|
| `synapse_weights` | `tuple[tuple[str, float], ...]` |
| `ifom_ttl_overrides` | `tuple[tuple[str, float], ...]` |
| `metadata` | `tuple[tuple[str, JsonScalar], ...]` |

`build_action_selection_context_from_snapshots()` materializes these tuples
from input `Mapping`s — subsequent mutation of the source mapping cannot
affect the context.

PFC/LC snapshot objects are inspected via `getattr` only (duck typing) —
no concrete type imports.

### Scoring policy

```
score = synapse_weight_factor * (synapse_weight or 0)
      + pfc_confidence_factor * (pfc_confidence or 0)
      + rpe_signal_factor * rpe_balance       # rpe_balance ∈ [-1, +1]
      + lc_caution_bonus                       # if NE ≥ 0.5 and defensive type

rpe_balance = clamp((positive - negative) / max(1, positive + negative), -1, +1)

final_score = clamp(score, 0.0, 1.0)
```

Default weights:
- `synapse_weight_factor = 0.4`
- `pfc_confidence_factor = 0.3`
- `rpe_signal_factor = 0.05`
- `lc_caution_bonus = 0.1`

**RPE counts are normalized** — raw positive/negative counts cannot dominate.

### Deterministic tie-breaker

1. score desc
2. candidate_type priority: `swarm_full > swarm_minimal > tier_1_5_augment > fallback`
3. `candidate_id` lex asc

### Confidence

```
no candidates → confidence = 0.0
1 candidate   → confidence = clamp(score, 0, 1)
≥2 candidates → confidence = clamp(top_score * 0.6 + margin * 0.4, 0, 1)
                where margin = top_score - second_score
```

### Logging

`bg.evaluated` payload:
- `trace_id`, `candidates_count`, `selected_id`, `selected_type`,
  `confidence`, `reason`, `category`, `applied=False`

`bg.error` payload:
- `trace_id`, `error_type`, `error`, `applied=False`

`module_name = "basal_ganglia"`. General logger failures are fail-open.
`asyncio.CancelledError` is always re-raised.

### Isolation Rules

| Module | Rule |
|--------|------|
| `app/basal_ganglia/*.py` | Allowed: `app.core.logging` + intra-package. Forbidden: `app.routing`, `app.execution`, `app.api`, `app.main`, `app.rpe`, `app.memory`, `app.synapse`, `app.maintenance`, `app.ingress`, LLM/embedder libs. |
| `app/routing/pfc.py` | Must NOT import `app.basal_ganglia` |
| `app/routing/lc.py` | Must NOT import `app.basal_ganglia` |
| `app/execution/swarm.py` | Must NOT import `app.basal_ganglia` |
| `app/api/routes.py` | Must NOT import `app.basal_ganglia` |
| `app/main.py` | Must NOT import `app.basal_ganglia` (no DI wiring) |
| `app/rpe/pipeline.py` | Must NOT import `app.basal_ganglia` |

### Out of Scope (deferred)

- **Conflict Resolution (CR)** — deferred to closeout / follow-up.
- PFC production-path integration.
- PlannerAgent / LC routing changes.
- AsyncSwarm / routes.py / main.py lifespan DI.
- `response_source` / SwarmTrace / QueryResponse changes.
- New RPE mutation target (still `synapse_weight` + `ifom_ttl`).
- Actual action selection application.
- 100-query regression run.

## Consequences

### Positive

- BasalGanglia logic is fully testable in isolation.
- ActionSelectionDecision.applied=False is enforced at the type level.
- Production behavior is provably unaffected (AST + source-grep tests).
- STEP 6 measurement can compare advisor output to actual flow decisions
  without integration risk.
- Scoring policy is deterministic — reproducible across runs.

### Negative

- Advisor recommendations are emitted but never consumed in STEP 5.1.
  Logs may grow if invoked outside test scope (it currently isn't).
- Tuple-of-pairs storage forces O(n) lookups in candidates' default build,
  but n is bounded (synapse categories ≤ ~10) so this is acceptable.
- Conflict Resolution remains deferred — multi-mutation arbitration is
  not addressed in this step.

## Resolution Plan

- **STEP 6**: Run 3-mode ablation (observe / dry_run / active) and measure
  advisor agreement with actual swarm decisions.
- **STEP 7 / closeout**: Evaluate whether to land Conflict Resolution and
  any PFC integration based on STEP 6 data.

## References

- `app/basal_ganglia/__init__.py`
- `app/basal_ganglia/models.py`
- `app/basal_ganglia/policies.py`
- `app/basal_ganglia/advisor.py`
- `tests/phase6/test_basal_ganglia_models.py`
- `tests/phase6/test_basal_ganglia_policy.py`
- `tests/phase6/test_basal_ganglia_advisor.py`
- `tests/phase6/test_basal_ganglia_context_builder.py`
- `tests/phase6/test_basal_ganglia_read_only.py`
- `tests/phase6/test_basal_ganglia_isolation.py`
- `tests/phase6/test_basal_ganglia_logging.py`
- `tests/phase6/test_step5_1_invariants.py`
- ADR-012 (RPE IFOM TTL Target Extension)
- ADR-011 (RPE Active Mutation Pipeline Integration)
- `docs/handoff/PHASE6_STEP5_1_CONTEXT.md`
