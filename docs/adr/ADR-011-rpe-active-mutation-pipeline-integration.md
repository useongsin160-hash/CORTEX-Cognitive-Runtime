# ADR-011: RPE Active Mutation Pipeline Integration (STEP 3.2)

## Status

Accepted (Phase 6 STEP 3.2)

## Context

Phase 6 STEP 3.1 delivered `RPEMutationService` as a standalone unit with no production pipeline integration. STEP 3.2 connects the service to the production execution path.

The key constraint: **production and default environments must never execute active mutation**. Only an explicit configuration override (not an environment variable) enables active mutation.

Integration risks:
- Modifying `routes.py` incorrectly could affect all query paths.
- Tight coupling to `AsyncSwarm` would prevent independent testing.
- Background task errors must not surface to callers.
- Early-exit paths (thalamus / cache / tier-1.5) must never trigger RPE.
- `SwarmResult` / `QueryResponse` schemas must remain unchanged.

## Decision

### Architecture: Decorator / Wrapper

`RPEMutationPipelineWrapper` (`app/rpe/pipeline.py`) wraps `AsyncSwarm.execute()`:

```
routes.py → rpe_pipeline.execute()
                ↓
         inner_swarm.execute()   ← unchanged
                ↓
         SwarmResult (returned unchanged)
                ↓ (only if enabled=True)
         asyncio.create_task(...)  ← fire-and-forget background RPE
```

### Scope (STEP 3.2)

- `RPEPipelineSnapshot` (new model in `models.py`): frozen snapshot of pipeline state
  built after `SwarmResult` is available.
- `RPEMutationPipelineWrapper` (`pipeline.py`): wraps inner swarm, builds snapshot,
  conditionally dispatches background task.
- `app/main.py`: DI wiring (disabled-by-default).
- `app/api/routes.py`: replace `state.async_swarm.execute()` with
  `state.rpe_pipeline.execute()` in both routed paths (main + continuation bypass).

### Out of Scope (STEP 3.3+)

- PFC state capture in snapshot (`pfc_active`, `pfc_cue_type`, `pfc_hint_applied`)
  — extension slots, set to `False`/`None` in STEP 3.2.
- Source aggregation.
- IFOM / PFC / Tier-1.5 / Epinephrine mutation.
- BasalGanglia / CR.
- Records persistence.
- Automatic rollback scheduler.

### Disabled-by-default

```python
# main.py — permanent production default
app.state.rpe_mutation_service = RPEMutationService(
    ...
    config=ActiveMutationConfig(enabled=False),
)
```

When `enabled=False`:
- No background task is ever created.
- `DopamineRPE.apply()` is never called.
- Zero overhead beyond building `RPEPipelineSnapshot` (cheap frozen dataclass).

### Background Task Safety

```python
async def _rpe_background(self, snapshot):
    try:
        await self._dopamine_rpe.apply(...)
    except asyncio.CancelledError:
        raise          # always re-raise
    except Exception:
        await self._safe_log_event(...)  # log, never propagate
```

Strong references in `self._background_tasks` prevent GC of in-flight tasks.

### Isolation Rules

| Component | Rule |
|-----------|------|
| `pipeline.py` | May import `app.api.schemas.*` + `app.execution.swarm_models` (data only). Must NOT import `app.execution.swarm` runtime or `app.api.routes`. |
| `routes.py` | Must NOT import `app.rpe.service` / `app.rpe.mutators` directly. Uses `state.rpe_pipeline` only. |
| `swarm.py` | Zero RPE imports. |
| `models.py` | Zero execution imports. |
| `main.py` | DI root — allowed to import all RPE modules. |

### RPEPipelineSnapshot Design

Fields map directly to `RPEContext`:

| Snapshot field | Source |
|----------------|--------|
| `trace_id` | Passed explicitly from routes.py |
| `session_id` | Passed explicitly from routes.py |
| `category` | `task_context.category` |
| `difficulty` | `int(task_context.difficulty)` |
| `response_source` | `"swarm"` (literal) |
| `latency_ms` | `swarm_result.total_elapsed_ms` |
| `error_occurred` | `"error" in [context_status, planner_status, generator_status]` |
| `timeout_occurred` | `"timeout" in statuses` |
| `continuation_bypass` | `task_context.continuation_context.detected` |
| `pfc_active` | `False` (STEP 3.3+ slot) |
| `pfc_cue_type` | `None` (STEP 3.3+ slot) |
| `pfc_hint_applied` | `False` (STEP 3.3+ slot) |

## Consequences

### Positive

- Production remains safe: `enabled=False` → zero mutation overhead.
- Background task errors never affect query responses (fail-open).
- Pipeline is now fully connected — enabling mutation requires only
  `ActiveMutationConfig(enabled=True)` with no code changes.
- `SwarmResult` / `QueryResponse` schemas unchanged (zero client impact).
- Wrapper pattern allows independent unit testing without FastAPI test client.
- Existing phase4/phase5 tests required only fixture updates
  (`rpe_pipeline._inner_swarm = spy`), not behavioral changes.

### Negative

- `_inner_swarm` is a mutable attribute — tests must save/restore it.
- PFC state not captured in STEP 3.2 (extension deferred).
- Snapshot is discarded when `enabled=False` (minor allocation overhead).

## Resolution Plan

- **STEP 3.3**: Capture PFC state in snapshot.
- **STEP 4**: IFOM TTL active mutation (target expansion).
- **STEP 5**: BasalGanglia / CR.
- **STEP 6**: 3-mode ablation measurement (observe / dry-run / active).
- **STEP 7**: Phase 6 closeout — records persistence, auto-rollback review.

## References

- `app/rpe/models.py` (`RPEPipelineSnapshot`)
- `app/rpe/pipeline.py` (`RPEMutationPipelineWrapper`)
- `app/main.py` (RPE DI wiring)
- `app/api/routes.py` (rpe_pipeline.execute() integration)
- `tests/phase6/test_rpe_pipeline_snapshot.py`
- `tests/phase6/test_rpe_mutation_pipeline_wrapper.py`
- `tests/phase6/test_rpe_pipeline_failure.py`
- `tests/phase6/test_rpe_pipeline_routes_integration.py`
- `tests/phase6/test_rpe_pipeline_lifespan.py`
- `tests/phase6/test_rpe_pipeline_isolation.py`
- ADR-010 (RPE Active Mutation Service)
- ADR-009 (RPE Dry-run Simulation)
- ADR-008 (RPE Data Model and Observe-only Mode)
- `docs/handoff/PHASE6_STEP3_2_CONTEXT.md`
