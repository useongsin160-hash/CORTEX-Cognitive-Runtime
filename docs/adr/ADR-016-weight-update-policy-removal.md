# ADR-016: Remove the Dead WeightUpdatePolicy Stub (B2)

## Status

Accepted (OVERTURE B2). Supersedes the `WeightUpdatePolicy` half of the
Phase 3.5 weight-update design; the canonical 7-cell updater is
[ADR-010](ADR-010-rpe-active-mutation-service.md)'s `RPEMutationService`.

## Context

`app/synapse/weights.py` held `WeightUpdatePolicy` with `apply_positive_rpe`
/ `apply_negative_rpe`, both raising `NotImplementedError("Phase 6 RPE 도입 시
활성화")`, plus a `_clip` helper. It was a Phase 3.5-era placeholder: the
intended mechanism for applying RPE-derived deltas to the production 7-cell
`SynapseState.weights` using **fixed deltas** (positive +0.15, consecutive
+0.10, negative −0.10; design doc 245-248).

Two facts make it dead and redundant:

1. **Never wired.** No production module instantiated or called it (grep:
   only the module itself, its stub test, and isolation guards referenced it;
   `app/synapse/__init__.py` did not export it).
2. **Already superseded.** Phase 6 STEP 3.1 (ADR-010) implemented the 7-cell
   weight update properly via `RPEMutationService` →
   `SynapseWeightMutator.apply_mutation` → `SynapseStoreAdapter.write_weight` →
   `SynapseState.weights[category]`, with a `PE × confidence × max_delta`
   calculus (not fixed deltas), single-apply, per-key locking, rollback records,
   and aiosqlite persistence. It is gated off (`active_enabled=False`) as the
   "absolute safety invariant." The RPE isolation tests explicitly forbade
   `app/rpe/*` from importing `app.synapse.weights` ("stub — not used directly"),
   i.e. the RPE path was built to replace it.

OVERTURE §0 무결점 condition 1 requires zero stubs / `NotImplementedError`. B2
must resolve this one. Reviving the stub (filling in `apply_*_rpe`) would create
a **second, divergent writer** of the same 7-cell state and reintroduce the
fixed-reward idea that B13 deliberately discarded in favor of observable,
process-derived signals — a regression. Therefore B2 = delete the dead stub.

## Decision

- **Delete** `app/synapse/weights.py` (`WeightUpdatePolicy` + `_clip`). `_clip`
  had no external consumer; clamping is already available via
  `WEIGHT_LOWER_BOUND` / `WEIGHT_UPPER_BOUND` (`app/synapse/categories.py`) and
  the RPE path's own `_clamp`.
- **Delete** `tests/phase3_5/test_weight_update_stub.py` (all five tests assert
  the now-removed class: two `NotImplementedError`, three `_clip`).
- **Remove the now-moot isolation guards** that named the deleted symbol — a
  forbidden-import guard against a non-existent module is dead config:
  - `tests/phase6/test_rpe_isolation.py` — drop `"app.synapse.weights"` from
    `FORBIDDEN_PREFIXES` **and** delete `test_no_weight_update_policy_runtime_import`.
  - `tests/phase6/test_active_mutation_isolation.py` — drop
    `"app.synapse.weights"` from `forbidden_synapse`.
  - `tests/phase6/test_dry_run_isolation.py` — delete
    `test_no_weight_update_policy_import`.

  (4 guard points across 3 files.)

  The remaining RPE isolation entries (observer / policies / snapshot / ifom /
  pfc / routes / main / basal_ganglia / cr / legacy / LLM libs) still enforce
  RPE independence.
- The canonical 7-cell synapse weight updater is `RPEMutationService` /
  `SynapseWeightMutator` (ADR-010), which stays gated (`active_enabled=False`);
  enabling it is the C gate-decision track, not B2.

## Consequences

### Positive

- One fewer `NotImplementedError` / dead stub — OVERTURE §0 condition 1.
- No duplicate / divergent 7-cell writer; single canonical RPE path.
- Honest docs: the audit no longer lists a non-existent organ.

### Negative / Neutral

- Production behavior change is zero — the removed code was never invoked.
- The fixed-delta (+0.15/+0.10/−0.10) idea is gone; if a fixed reward→delta
  mapping is ever wanted it belongs in the RPE source/calculator layer, not a
  parallel `SynapseState` writer (and would still be subject to B13's
  process-signal stance).

## References

- `app/rpe/mutators.py` (`SynapseWeightMutator`, `SynapseStoreAdapter`) — canonical updater
- `app/synapse/store.py` (`SynapseState`, 7-cell)
- ADR-010 (RPE Active Mutation Service — the supersedor)
- ADR-008 / ADR-009 (point-in-time records; unchanged)
- OVERTURE_v1_0_MASTER_PLAN.md (B2 row)
