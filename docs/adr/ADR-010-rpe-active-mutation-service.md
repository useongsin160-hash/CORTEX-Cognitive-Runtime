# ADR-010: RPE Active Mutation Service (STEP 3.1)

## Status

Accepted (Phase 6 STEP 3.1)

## Context

Phase 6 STEP 1/2에서 RPE observe-only와 dry-run을 구현했다. STEP 3는 실제 Synapse weight mutation을 도입한다.

위험 요소:

- 첫 실제 mutation: 회귀 가능성
- `SynapseStore` 통합: 격리 패턴 일부 완화 필요
- Rollback 미검증: 복원 실패 시 시스템 오염
- Source aggregation 미해결: mock + heuristic 동시 적용 위험
- Production pipeline 통합: routes/swarm/main 동시 변경 위험

따라서 STEP 3는 두 단계로 분할한다:

- **STEP 3.1**: `RPEMutationService` 단독 구현 (이번)
- **STEP 3.2**: Pipeline 통합 (후속)

## Decision

### Scope (STEP 3.1)

- `RPEMutationService` 신규 (`app/rpe/service.py`)
- `SynapseWeightMutator` 신규 + `SynapseWeightStoreProtocol` + `InMemorySynapseWeightStore` + `SynapseStoreAdapter` (`app/rpe/mutators.py`)
- `ActiveMutationConfig` (disabled-by-default)
- `RPEMutationRecord` (pre/post weight + rollback metadata)
- `DopamineRPE.apply()` wrapper (테스트 편의용, production entry point 아님)
- Manual rollback method
- Service 단독 테스트 (InMemory / Mock store 사용)

### Out of Scope (STEP 3.2 이후)

- `routes.py` 통합
- `AsyncSwarm` 통합
- `main.py` lifespan DI
- Background task 호출
- 자동 timeout rollback (scheduler)
- Source aggregation
- IFOM / PFC / Tier-1.5 / Epinephrine mutation

### Thresholds

```
confidence >= 0.5
abs(prediction_error) >= 0.3
abs(proposed_delta) > 0
```

이 셋 중 하나라도 미달이면 `rpe.active_blocked`로 차단한다.

### Per-trace-target Single-apply

같은 `(trace_id, target_key)`에는 1개 mutation만 적용한다.

선택 우선순위 (aggregation 아님 — selection):

1. `confidence` 높은 proposal
2. 동률 시 `source="mock"` 우선
3. 동률 시 `abs(proposed_delta)` 큰 proposal
4. 그래도 동률이면 `rollback_id` lexicographic order (deterministic tie-break)

선택되지 않은 proposal은 `rpe.active_blocked(reason="duplicate_target", competing_rollback_id=…)`로 기록한다.

### Locking — internal per-key registry

`lock_key = f"synapse_weight:{target_key}"` 단위로 cross-trace 직렬화.

**`app.core.lock_manager.LockManager` 사용 안 함.** LockManager 키는 `trace_id + field_name`이라서 cross-trace category 직렬화에 부적합. `RPEMutationService` 내부에 `dict[str, asyncio.Lock]` registry를 둔다 (LockManager 수정 0건).

Lock timeout: 기본 1초 (`ActiveMutationConfig.lock_timeout_ms`).

### Rollback

- Manual only — `service.rollback(rollback_id)` 호출.
- 자동 rollback scheduler **구현 금지**.
- `RPEMutationRecord.expires_at` / `rollback_status` 슬롯만 마련.
- 이미 `rolled_back` / `expired` record에 대한 rollback은 idempotent (no-op + 기존 record 반환).

### current_values는 STALE hint

STEP 2에서 dry-run의 `current_values`는 외부 주입이었다. STEP 3.1에서는:

- caller의 `current_values`는 stale hint로 취급.
- `RPEMutationService`는 **lock 획득 후 `mutator.read_current_weight()`로 store에서 다시 읽는다**.
- store 값이 authoritative. mismatch 시 `current_value_mismatch=True`로 기록하되 store 값으로 mutation을 진행한다.

### Disabled-by-default

`ActiveMutationConfig.enabled = False` 기본값. 모든 production 환경은 explicit enable 필요. Pipeline 통합(STEP 3.2)에서도 explicit enable이 강제된다.

`enabled=False`일 때 `apply_proposals()`는 빈 리스트를 반환하고 각 proposal에 대해 `rpe.active_skipped(reason="disabled")`를 기록한다.

### Logging Events

| event | trigger | required payload keys |
|-------|---------|----------------------|
| `rpe.active_applied` | mutation 성공 | session_id, source, target, target_key, previous_value, proposed_delta, applied_delta, new_value, max_delta, rollback_id, confidence, prediction_error, lock_key, applied_at, current_value_mismatch |
| `rpe.active_blocked` | threshold/duplicate/lock_timeout | source, target_key, reason, (+ competing_rollback_id for duplicate_target) |
| `rpe.active_skipped` | disabled | source, target_key, reason |
| `rpe.active_rollback` | manual rollback | rollback_id, target_key, previous_value, current_value_before_rollback, restored_value, rolled_back_at |
| `rpe.active_error` | read/write/rollback exception | source, target_key, error_type, error, phase |

`module_name = "rpe_mutation_service"`.

### Isolation rules (relaxed for STEP 3.1)

- `app.synapse`: `mutators.py`에서 adapter 패턴 허용. `service.py`는 직접 import 금지 (mutator만 호출).
- `app.api.routes` / `app.execution.swarm` / `app.main` / `app.memory` / `app.routing`: 여전히 금지.
- `BasalGanglia` / `ConflictResolution`: 여전히 금지.
- LLM / embedder / legacy: 여전히 금지.

## Consequences

### Positive

- Service 단독 검증으로 첫 active mutation의 안전성을 확보.
- per-trace-target rule로 source aggregation 결정을 STEP 6 이후로 미루면서도 첫 mutation은 가능.
- Manual rollback으로 복원 메커니즘을 검증.
- `disabled=False` 기본값 + production pipeline 통합 0건으로 회귀 위험 0.
- `current_values` re-read로 stale hint에 의한 잘못된 mutation 방지.
- `LockManager` 수정 없이 cross-trace category 직렬화 달성.

### Negative

- STEP 3.1만으로는 실제 학습 효과 0건 (pipeline 미통합).
- Records in-memory라 service 재시작 시 rollback 이력 손실.
- 자동 rollback 부재 — manual 호출이 없으면 mutation은 영구 적용.
- pipeline 통합(STEP 3.2)까지 한 단계 더 필요.

## Resolution Plan

- **STEP 3.2**: Pipeline 통합 — routes/swarm/main background task에 service 주입, explicit feature flag.
- **STEP 4**: IFOM TTL active mutation 검토 (target 확장).
- **STEP 5**: BasalGanglia / CR.
- **STEP 6**: Active mutation 효과 측정 (3-mode ablation: observe / dry-run / active).
- **STEP 7**: Phase 6 closeout. Records 영속화 / 자동 rollback 도입 여부 재검토.

## References

- `app/rpe/models.py` (ActiveMutationConfig, RPEMutationRecord)
- `app/rpe/mutators.py` (SynapseWeightMutator, InMemorySynapseWeightStore, SynapseStoreAdapter)
- `app/rpe/service.py` (RPEMutationService)
- `app/rpe/dopamine.py::DopamineRPE.apply` (wrapper)
- `tests/phase6/test_active_mutation_config.py`
- `tests/phase6/test_rpe_mutation_record.py`
- `tests/phase6/test_synapse_mutator.py`
- `tests/phase6/test_rpe_mutation_service.py`
- `tests/phase6/test_dopamine_apply.py`
- `tests/phase6/test_active_mutation_logging.py`
- `tests/phase6/test_active_mutation_isolation.py`
- `tests/phase6/test_active_mutation_safety.py`
- `tests/phase6/test_step3_1_pipeline_isolation.py`
- ADR-008 (RPE Data Model and Observe-only Mode)
- ADR-009 (RPE Dry-run Simulation)
- `docs/handoff/PHASE6_STEP2_CONTEXT.md`
- `app/synapse/store.py` (Production store wrapped by SynapseStoreAdapter)
- `app/synapse/weights.py::WeightUpdatePolicy` (STEP 3.1에서 직접 호출하지 않음 — Phase 6 후반 검토)
