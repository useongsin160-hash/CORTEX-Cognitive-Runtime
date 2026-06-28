# ADR-012: RPE IFOM TTL Target Extension (STEP 4)

## Status

Accepted (Phase 6 STEP 4)

## Context

Phase 6 STEP 3.2 delivered `RPEMutationPipelineWrapper` integrating RPE into
the production path with only `synapse_weight` as the active mutation target.
STEP 4 extends the mutation target space to include **IFOM TTL** — session/
category-scoped overrides of `IFOMPolicy` TTL values.

Key constraints:
- **Global `IFOMConfig` is never mutated.** Only per-(session_id, category,
  ttl_type) overrides are stored in `IFOMTTLOverrideStore`.
- **IFOMPolicy public method signatures unchanged.** `cleanup_expired`,
  `evaluate_goal`, `adjust_ttl_with_rpe_hook` remain sync.
- **disabled-by-default**. Same `ActiveMutationConfig(enabled=False)` applies.
- `RPEMutationPipelineWrapper` has ZERO code changes.
- `routes.py`, `swarm.py`, `app/synapse`, `app/api/schemas` have ZERO changes.

## Decision

### New Module: `app/rpe/ifom_store.py`

Pure data module with:

- `IFOMTTLType = Literal["active", "paused", "completed", "low_priority"]`
- `IFOMTTLOverride`: frozen dataclass per (session_id, category, ttl_type).
- `IFOMTTLOverrideStoreProtocol`: **sync** Protocol (matches IFOMPolicy sync API).
- `InMemoryIFOMTTLOverrideStore`: deterministic test backend.
- `build_ifom_ttl_target_key(ttl_type, category) → "{ttl_type}:{category}"`
- `parse_ifom_ttl_target_key(key) → (ttl_type, category)`

Target key format: `"{ttl_type}:{category}"` (e.g. `"active:coding"`).
Lock key format (set by service): `"ifom_ttl:{target_key}"`.

### IFOMTTLType — 4 values including "paused"

```
"active"       → IFOMConfig.active_ttl_seconds
"paused"       → IFOMConfig.paused_ttl_seconds
"completed"    → IFOMConfig.completed_ttl_seconds
"low_priority" → IFOMConfig.low_priority_ttl_seconds
```

### Model Extensions

**`DryRunConfig`** (STEP 4 additions):
- `ifom_ttl_max_delta: float = 300.0` (5 minutes)
- `ifom_ttl_min_seconds: float = 60.0` (1 minute lower bound)
- `ifom_ttl_max_seconds: float = 86400.0` (24 hours upper bound)
- Drops the `synapse_weight`-required constraint — any non-empty target
  subset is valid (enables `enabled_targets=("ifom_ttl",)` configs).

**`ActiveMutationConfig`** (STEP 4 additions):
- `ifom_ttl_min_seconds: float = 60.0`
- `ifom_ttl_max_seconds: float = 86400.0`

**`RPEProposal`** (STEP 4 extension):
- `target` now accepts `"synapse_weight"` **or** `"ifom_ttl"`.
- Previously only `"synapse_weight"` was valid (STEP 2 invariant relaxed).

**`RPEMutationRecord`** (STEP 4 extension):
- Lock key validation is now **target-aware**:
  - `synapse_weight` → `lock_key.startswith("synapse_weight:")`
  - `ifom_ttl` → `lock_key.startswith("ifom_ttl:")`
- `weight_min` / `weight_max` fields reused as generic value bounds
  (hold TTL seconds for `ifom_ttl` target).

### New Class: `IFOMTTLDryRunCalculator` (in `calculators.py`)

Same delta formula as `SynapseWeightDryRunCalculator`:
```
proposed_delta = clamp(pe × conf × ifom_ttl_max_delta, ±max_delta)
proposed_value = clamp(current + delta, ttl_min, ttl_max)
```

`compute_proposal(decision, ttl_type, current_value=None)` produces one
`RPEProposal(target="ifom_ttl")` per ttl_type, or `None` on skip.

### New Class: `IFOMTTLMutator` (in `mutators.py`) — **sync**

```python
def read_current_ttl(session_id, target_key) -> float | None
def apply_mutation(proposal, previous_value, lock_key, ...) -> RPEMutationRecord
def rollback(record) -> RPEMutationRecord
```

Sync because `IFOMPolicy.adjust_ttl_with_rpe_hook` is sync. Writes
`IFOMTTLOverride` objects to `IFOMTTLOverrideStoreProtocol`. Never writes
to `IFOMConfig`.

### `RPEMutationService` dispatch (STEP 4 extension)

New parameter: `ifom_mutator: IFOMTTLMutator | None = None`.

In `_apply_with_lock`:
- `synapse_weight` → existing async `self._mutator` path (unchanged).
- `ifom_ttl` → sync `self._ifom_mutator` path (direct call, O(1)).
  - Falls back to `current_values[target_key]` if no override in store.
  - Logs `rpe.active_blocked` reason `"no_ifom_mutator"` if mutator is None.

### `DopamineRPE.dry_run` — target-agnostic (STEP 4)

For each `RPEDecision`:
1. If `"synapse_weight"` in `enabled_targets`: compute synapse_weight proposal
   (unchanged from STEP 2).
2. If `"ifom_ttl"` in `enabled_targets`: compute 4 proposals (one per TTL type
   via `IFOMTTLDryRunCalculator`).

`current_values` keys for ifom_ttl: `"{ttl_type}:{category}"`.

### `IFOMPolicy` minimal extension (STEP 4)

New optional parameter:
```python
ttl_override_resolver: Callable[[str | None, str | None, str], float | None] | None = None
```

`adjust_ttl_with_rpe_hook(goal, base_ttl)` → queries resolver with
`(goal.session_id, goal.category, ttl_type)`. Returns override if found,
otherwise returns `base_ttl` (exact STEP 5 behavior preserved when resolver=None).

The resolver is a `Callable` — no direct import of `app.rpe.*` in `ifom.py`.

Low-priority goals map to `"low_priority"` TTL type; others use `goal.status`.

### Isolation Rules

| Module | Rule |
|--------|------|
| `ifom_store.py` | No import from `app.memory`, `app.rpe.service`, `app.rpe.dopamine`, `app.rpe.mutators`, `app.synapse`, `app.api`, `app.execution`, `app.main`, `app.routing` |
| `app/memory/ifom.py` | No import from `app.rpe.*` (resolver is Callable) |
| `pipeline.py` | ZERO changes — same isolation rules as STEP 3.2 |
| `routes.py` | ZERO changes |
| `swarm.py` | ZERO changes |

### Production DI (main.py)

```python
app.state.ifom_ttl_store = InMemoryIFOMTTLOverrideStore()
app.state.ifom_ttl_mutator = IFOMTTLMutator(store=app.state.ifom_ttl_store)
app.state.rpe_mutation_service = RPEMutationService(
    mutator=app.state.rpe_mutator,
    logger=get_spinal_logger(),
    config=ActiveMutationConfig(enabled=False),  # NEVER True in production
    ifom_mutator=app.state.ifom_ttl_mutator,
)
```

## Consequences

### Positive

- IFOM TTL active mutation is fully wired without changing pipeline, routes,
  or swarm code.
- Enabling requires only `ActiveMutationConfig(enabled=True)` — zero code changes.
- Global `IFOMConfig` is provably unmodified (frozen dataclass + AST tests).
- `IFOMPolicy` sync contract preserved — no async conversion needed.
- Existing synapse_weight mutation path is unaffected.

### Negative

- `DopamineRPE.dry_run` produces up to 4× more proposals when `"ifom_ttl"` is
  enabled (one per TTL type per decision source).
- `current_values` caller must provide TTL hint for ifom_ttl to work when
  no existing override is stored. STEP 3.3 provides `SynapseStoreAdapter`
  fallback; IFOM TTL fallback deferred to STEP 5+.
- `weight_min` / `weight_max` field names in `RPEMutationRecord` are
  repurposed for generic bounds (misleading for `ifom_ttl` records).

## Resolution Plan

- **STEP 3.3**: PFC state capture in snapshot (existing debt).
- **STEP 5**: IFOM TTL current_values populated from `SynapseStoreAdapter`;
  BasalGanglia / CR.
- **STEP 6**: 3-mode ablation measurement.
- **STEP 7**: Records persistence, auto-rollback review.

## References

- `app/rpe/ifom_store.py` (IFOMTTLType, IFOMTTLOverride, store)
- `app/rpe/calculators.py` (IFOMTTLDryRunCalculator)
- `app/rpe/mutators.py` (IFOMTTLMutator)
- `app/rpe/service.py` (ifom_ttl dispatch)
- `app/rpe/dopamine.py` (target-agnostic dry_run)
- `app/memory/ifom.py` (ttl_override_resolver extension)
- `app/main.py` (IFOM TTL DI)
- `tests/phase6/test_ifom_ttl_target_key.py`
- `tests/phase6/test_ifom_ttl_override_store.py`
- `tests/phase6/test_ifom_ttl_calculator.py`
- `tests/phase6/test_ifom_ttl_mutator.py`
- `tests/phase6/test_ifom_policy_override.py`
- `tests/phase6/test_rpe_service_ifom_ttl.py`
- `tests/phase6/test_rpe_pipeline_ifom_ttl.py`
- `tests/phase6/test_ifom_ttl_record_validation.py`
- `tests/phase6/test_ifom_ttl_isolation.py`
- `tests/phase6/test_step4_invariants.py`
- ADR-011 (RPE Active Mutation Pipeline Integration)
- ADR-010 (RPE Active Mutation Service)
- ADR-006 (IFOM TTL + Status-based Forgetting Policy)
- `docs/handoff/PHASE6_STEP4_CONTEXT.md`
