# ADR-015: BasalGanglia Production Wiring (B7)

## Status

Accepted (OVERTURE B7) — partially supersedes the isolation table of
[ADR-013](ADR-013-basal-ganglia-advisor.md).

## Context

ADR-013 (STEP 5.1) delivered the `BasalGangliaAdvisor` fully isolated:
`PFC / LC / Swarm / routes / main / RPE pipeline` were forbidden (AST +
source-grep) from importing `app.basal_ganglia`, so the advisor's
recommendation was emitted but **never consumed** in production. B6 measured
the advisor only inside the ablation harness, on **synthetic** difficulty-derived
features.

OVERTURE routes the un-started B work items (B1·B2·B7·B8·B9) before the C gate
decision. B7 is the first: connect the existing advisor to the production
pipeline so its recommendation runs on **real** pipeline snapshots and is
measurable, **without** changing any routing/execution behavior. Per the
OVERTURE mode table, "BasalGanglia: 배선+측정 완료, `applied=False` 유지" — the
actual Go/No-Go application stays frozen for the C gate (C2), mirroring B13's
`difficulty_learning_enabled=False` freeze ("wire but freeze; C activates").

## Decision

### One-way production wiring

`app/main.py` injects `app.state.basal_ganglia = BasalGangliaAdvisor(...)` and
`app/api/routes.py` invokes it once per routed query, **after** the final
`route_path` is fixed (post-ratchet), on **both** the normal and continuation
paths. Import direction is strictly one-way:

| Module | ADR-013 (STEP 5.1) | ADR-015 (B7) |
|--------|--------------------|--------------|
| `app/main.py` | MUST NOT import `app.basal_ganglia` | **imports** (DI wiring) |
| `app/api/routes.py` | MUST NOT import `app.basal_ganglia` | **imports** (advisory call) |
| `app/routing/pfc.py` · `lc.py` | MUST NOT import | **unchanged — still forbidden** |
| `app/execution/swarm.py` | MUST NOT import | **unchanged — still forbidden** |
| `app/rpe/pipeline.py` | MUST NOT import | **unchanged — still forbidden** |
| `app/basal_ganglia/*.py` | MUST NOT import app.api/app.main/app.routing/… | **unchanged — still forbidden** |

The advisor stays a leaf: it never imports the live layers (its snapshot /
duck-typing design depends on this). The reversal is limited to `main` + `routes`
importing **into** BG.

### Recommendation-only — applied=False double-lock preserved

1. **Type hard-lock (unchanged):** `ActionSelectionDecision.applied` still raises
   `ValueError` if `True`. The advisor cannot emit an applied decision.
2. **Wiring freeze (B7 adds):** routes calls `evaluate()` and the recommendation
   is logged (`bg.evaluated`) only; it is **never** read back into
   `route_path` / `skip_layers` / `selected_tier` / answer. Execution is still
   100% decided by skip_router → override → ratchet.

### Telemetry-only surface

No `TaskContext` / `QueryResponse` / `SwarmTrace` field is added. The
recommendation surfaces solely through the advisor's existing `bg.evaluated`
spinal trace. `app.state` gains exactly one attribute (`basal_ganglia`); no
`bg_*` state is added.

### Honest None/0 degradation (no invented input)

At the insertion point production snapshots are partial. They degrade to
None/0 as a matter of fact — **no feature is fabricated** (preserves the B6
manipulation boundary: a measurer must not invent its own inputs):

| BG context field | Sync-available? | Degraded to | Why |
|---|---|---|---|
| `category` / `difficulty` / `synapse_weights` | yes | real values | `task_context` |
| `pfc_*` | no | `pfc_snapshot=None` → None/False | PFC is async-dispatched by LC; not joined in routes |
| `lc_ne_level` / `lc_intent_label` | no | `lc_snapshot=None` → None | LC exposes `ne_boost` (bool), no float `ne_level` — a bool→float mapping would be invented input |
| `rpe_recent_*_count` | no | `0` | no production RPE-history surface |

`synapse_snapshot` is the only real signal; the advisor recommends from it
alone. The getattr-safe builder absorbs all None values with no branching code.

### Fail-open

The whole pass (context build + `evaluate`) is wrapped fail-open in routes
(`CancelledError` re-raised, all else swallowed). The context builder validates
`synapse_weights ∈ [0,1]` and may raise on an out-of-range snapshot; the wrapper
guarantees an advisory failure can never break the request.

### C2 unfreeze (out of scope here)

Enabling actual Go/No-Go application is C2: it requires (a) a
`candidate_type ↔ route_path` mapping (deferred to C) and (b) a separate
applier consuming the recommendation, gated by a wiring config flag — the model
hard-lock (`applied=False`) is recommended to stay. B7 lands neither.

## Consequences

### Positive

- Production BG now runs on real snapshots → `bg.evaluated` is the
  production-fidelity counterpart of B6's raw observation (`bg_recommended` vs
  actual `route_path`). C reads this to decide the mapping and No-Go safety.
- Behavior change is provably zero (applied=False double-lock; telemetry-only;
  schema unchanged) — verified by AST + source-grep + wiring tests.
- Added cost per routed query is O(1): 4-candidate scoring, no I/O / LLM / DB.

### Negative

- The recommendation is built on partial features (synapse-only) until more
  snapshots are wired; this is intentional and is exactly why apply stays frozen
  and measurement comes first.
- `test_step5_1_invariants.py` and `test_basal_ganglia_isolation.py` lose their
  "routes/main never reference BG" guarantee; the relevant assertions are
  inverted to the B7 one-way form (the inner-layer + BG-leaf guards remain).

## References

- `app/main.py` (DI), `app/api/routes.py` (`_basal_ganglia_observe`)
- `app/basal_ganglia/advisor.py` (`build_action_selection_context_from_snapshots`)
- `tests/phase6/test_basal_ganglia_wiring.py` (new)
- `tests/phase6/test_basal_ganglia_isolation.py` (one-way flip)
- `tests/phase6/test_step5_1_invariants.py` (B7 reference/attr flip)
- ADR-013 (BasalGanglia Advisor — isolation table partially superseded)
- ADR-014 (Conflict Resolution — Deferred; `applied=False` safety rail intact)
