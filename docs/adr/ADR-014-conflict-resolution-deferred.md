# ADR-014: Conflict Resolution — Deferred

## Status

Proposed / Deferred (Phase 6 Closeout)

## Context

Phase 6 STEP 5.1 introduced the BasalGanglia Advisor layer
(`app/basal_ganglia/`) as a **read-only / recommendation-only** component.
The design plan for STEP 5 was originally split into two sub-steps:

- **STEP 5.1**: BasalGanglia Advisor — scoring, selection, recommendation,
  logging. **Completed.**
- **STEP 5.2**: Conflict Resolution (CR) — arbitration when multiple advisors
  or mutation proposals conflict. **Deferred (this ADR).**

Conflict Resolution was intentionally excluded from STEP 5.1 to avoid
prematurely committing to an arbitration strategy before production
measurement data is available.

The deferred decision is recorded here so the gap is explicit and
tracked.

## Problem Statement

In the current system, multiple subsystems may independently propose
mutations or action recommendations:

1. **RPE Synapse mutation** (`RPEMutationService`, target `synapse_weight`)
2. **RPE IFOM TTL mutation** (`IFOMTTLMutator`, target `ifom_ttl`)
3. **BasalGanglia action recommendation** (`BasalGangliaAdvisor`, applied=False)

In STEP 5.1, BasalGanglia is read-only and its decisions are never
applied, so there is no actual conflict today.

A conflict can arise when BasalGanglia becomes production-integrated (STEP 6+)
and its selected action implies a different synapse weight or IFOM TTL
than what the RPE pipeline has already applied (or is about to apply).

The current system handles this by:
- RPE mutations: per-trace-target single-apply rule + per-key asyncio.Lock
- BasalGanglia: applied=False (never executes)
- No shared arbitration layer

## Decision (Deferred)

**No Conflict Resolution implementation in Phase 6.**

The rationale:

1. BasalGanglia is not yet production-integrated; CR would arbitrate
   nothing today.
2. STEP 6 measurement will generate agreement-rate data between
   BasalGanglia recommendations and actual swarm decisions. This data
   should guide CR strategy selection.
3. Premature CR risks introducing state-machine complexity without
   a clear benefit target.

Three candidate strategies are on record for future evaluation:

| Strategy | Description |
|----------|-------------|
| **CR-A: Veto** | BasalGanglia veto blocks conflicting RPE mutations. |
| **CR-B: Priority Queue** | All proposals enter a priority queue; BG arbitrates based on confidence. |
| **CR-C: Overlay** | BG recommendation acts as an overlay weight modifier on top of RPE delta. |

No strategy is chosen; selection deferred to post-STEP-6 data review.

## Preconditions for Activation

Conflict Resolution MUST NOT be activated until:

1. STEP 6 measurement harness (3-mode ablation: observe / dry_run / active)
   is implemented and data collected.
2. BG agreement rate with actual swarm decisions is measured per category.
3. A deliberate decision is made on CR strategy (A, B, or C above).
4. `ActionSelectionDecision.applied` guard is relaxed — currently
   hard-locked to `False` at the type level.
5. Production pipeline integration (`BasalGangliaAdvisor` DI in `main.py`
   lifespan) is completed.

## Out of Scope Until CR Is Activated

- Any code in `app/` that sets `ActionSelectionDecision.applied=True`
- BasalGanglia import in `app/routing/`, `app/execution/`, `app/api/`,
  `app/main.py`, `app/rpe/pipeline.py`
- Any new RPE mutation target beyond `{"synapse_weight", "ifom_ttl"}`
  that CR would dispatch to

## Consequences

### Positive

- Phase 6 closes cleanly with zero incomplete / half-committed features.
- CR design can be evidence-driven (STEP 6 measurement data).
- Existing `applied=False` invariant continues to provide a hard safety
  rail during the deferred period.

### Negative

- Multi-system arbitration is unaddressed. If RPE and BG disagree on
  synapse weight direction, both act independently (RPE mutates; BG
  only recommends). This is acceptable because BG is currently
  recommendation-only and RPE is disabled-by-default in production.

## References

- `docs/adr/ADR-013-basal-ganglia-advisor.md`
- `docs/adr/ADR-010-rpe-active-mutation-service.md`
- `docs/adr/ADR-012-rpe-ifom-ttl-target-extension.md`
- `docs/handoff/PHASE6_STEP5_1_CONTEXT.md`
- `docs/handoff/PHASE6_CLOSEOUT_CONTEXT.md`
