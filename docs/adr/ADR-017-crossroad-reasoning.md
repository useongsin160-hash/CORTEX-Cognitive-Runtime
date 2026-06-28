# ADR-017: Crossroad Reasoning (B8)

## Status

Accepted (OVERTURE B8). Distinct from — and does NOT supersede —
[ADR-014](ADR-014-conflict-resolution-deferred.md).

## Context

The audit lists this component as "Conflict Resolution (CR / Crossroad
Reasoning)". The two readings are different organs:

- **ADR-014 "Conflict Resolution"** = *arbitration* between conflicting mutation
  proposals (BasalGanglia veto / priority queue / overlay). Still Deferred.
- **This ADR "Crossroad Reasoning" (갈림길 추론)** = *exploration injection* at a
  near-tie routing decision. This is B8.

The master plan's "PFC/LC/Synapse 충돌 탐지" phrasing followed the arbitration
reading; B8 deliberately implements the Crossroad Reasoning intent instead.

The RPE override (B11 S3a) already maps the learned (category, difficulty) weight
to one of three route bands (`lightweight` / `standard` / `full_pipeline`) using
thresholds 0.4 (demote) and 0.7 (promote). When the weight sits right at a
threshold, the routing decision is a near-tie, yet the system always takes the
#1 band — an exploit lock-in that never gathers evidence for the road not taken.

## Decision

Add `CrossroadReasoner` (`app/routing/crossroad.py`). After the main routed
response, for each request:

1. **Crossroad test.** Read the 35-cell weight. If it is within `cr_margin`
   (default 0.05, absolute) of a band threshold (0.4 or 0.7) AND an adjacent band
   exists (ladder not at an edge), this is a crossroad. An unlearned cell (weight
   `None`) is never a crossroad — the B12 path stands.
2. **Mode.** Emergency mode (`epinephrine_active` / `ne_boost`, i.e. limit-break
   or difficulty ≥ 4) → CR off. Explore mode (PFC-directed) is structure-only:
   the PFC explore signal is not surfaced synchronously (B7 debt), so it is never
   reached. Stable mode (default) → step 3.
3. **Probabilistic fire.** In stable mode, fire at `cr_stable_probability`
   (default 0.10).
4. **Background explore.** Run the adjacent (loser) band through the injected
   runner (`rpe_pipeline.execute`) on a distinct sub-trace
   (`{trace_id}::cr_explore`), fire-and-forget. The user answer is always the #1
   band's; the explore's only purpose is its post-response learning side-effect.

### Candidates = route bands (not plan variants)

The planner stays single-plan; "candidates" are the three route bands the RPE
weight already ranks. The #1 band is the override's pick; the explore is an
adjacent band. No multi-candidate planner, no per-plan scoring — the largest
structural change is avoided.

### Cost

Explore = a second swarm pass, but amortized by `stable_probability` (fires only
on ~10% of crossroads) and lightened when the explore band is `lightweight` (the
swarm skips Context retrieval). Worst case (explore = `full_pipeline`) is a full
second pass, and only when not in emergency mode.

### Explore → learning, single-apply separation

The explore reuses the existing `RPEDifficultyLearner` via `rpe_pipeline.execute`
— no new learning logic. The distinct sub-trace keeps the explore's mutation off
the main run's `(trace_id, target_key)` single-apply key, so the two never
collide. The explore mutation is a normal mutation (B3 persistence, B4 rollback,
S5 decay all apply unchanged). The 35-cell is keyed (category, difficulty), so the
explore's reward updates the same cell the override reads — the exploit/explore
feedback loop.

### Doubly frozen (C3 activates)

`cr_enabled=False` freezes the explore execution; and the explore's learning is
gated again by `difficulty_learning_enabled` (B13 freeze). So pre-C3 nothing
fires and nothing learns. C3 flips both.

### Leaf isolation

`crossroad.py` imports the difficulty-store protocol (read) + `RouteDecision` +
`TaskContext` + logging only. The explore runner is injected, so it imports
neither `app.rpe.pipeline` nor `app.execution.swarm`. No LLM/embedder imports;
the explore reaches an LLM only through the existing swarm.

## Consequences

### Positive

- Exploration data for the RPE 35-cell without a multi-candidate planner.
- Behavior change is zero pre-C3 (doubly frozen); the explore never affects the
  user-facing answer even when enabled.
- Reuses swarm + learner; new learning logic is zero.

### Negative / debt

- Explore mode (PFC-directed, 50%) is structure-only until the PFC explore signal
  is surfaced (B7 debt). Stable is the only live mode.
- The explore cost is a second swarm pass when it fires (amortized, lightened for
  the lightweight band, but real).
- Cell-level (not band-level) reward attribution: the explore updates the
  (category, difficulty) cell, a coarse but functional feedback.

## References

- `app/routing/crossroad.py`
- `app/routing/rpe_route_override.py` (band ladder + thresholds mirrored)
- `app/rpe/difficulty_store.py` (35-cell weight read)
- `app/rpe/difficulty_learner.py` / `app/rpe/pipeline.py` (reused explore learning)
- ADR-014 (Conflict Resolution / arbitration — separate, still Deferred)
- ADR-011 (RPE active mutation pipeline integration)
