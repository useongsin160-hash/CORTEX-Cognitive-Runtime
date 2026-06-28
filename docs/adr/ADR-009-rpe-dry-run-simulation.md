# ADR-009: RPE Dry-run Simulation

## Status

Accepted (Phase 6 STEP 2)

## Context

Phase 6 STEP 1에서 RPE 데이터 모델과 observe-only 관측을 도입했다.
STEP 2는 mutation 이전에 RPE가 제안하는 변경을 시뮬레이션한다.

실제 mutation은 아직 위험하다:
- 잘못된 신호로 Synapse weight 오염 가능
- rollback 메커니즘 미검증
- source별 proposal aggregation 정책 미정
- active mutation 측정 데이터 없음

따라서 STEP 2는 **변경 제안만 계산하고 절대 적용하지 않는다**.

## Decision

### Model Separation

`RPEDecision`은 observe-only invariant를 유지한다. `mode`, `target`,
`proposed_delta`, `rollback_id`, `applied` 필드는 STEP 1 이후로도
locked 상태다.

dry-run 결과는 **별도 `RPEProposal` 모델**로 표현한다.
`RPEDecision.mode="dry_run"` 허용 금지.

### Scope — Synapse weight only

`DryRunConfig.enabled_targets = ("synapse_weight",)` 기본값.
IFOM TTL, PFC timeout/confidence, Tier-1.5, Epinephrine은 후속 단계 슬롯.

### Delta Formula

```
proposed_delta = clamp(
    prediction_error * confidence * max_delta,
    -max_delta, +max_delta
)
```

예시 (max_delta=0.1):

| prediction_error | confidence | proposed_delta |
|-----------------|-----------|---------------|
| +0.6 | 0.3 | +0.018 |
| +1.0 | 1.0 | +0.100 (clamped) |
| -0.5 | 0.5 | -0.025 |
| 0.0 | any | 0.000 |

보수적 공식을 선택한 이유:
- confidence 0.3 (Heuristic 기본값)에서 최대 30% 감쇄
- max_delta 0.1 × confidence 0.3 = 실제 최대 0.03

### Synapse Weight Bounds

```
weight_min = 0.1, weight_max = 1.0
proposed_value = clamp(current_value + proposed_delta, 0.1, 1.0)
```

### Target Key

```
target_key = f"category:{context.category}"
```

skip 조건:
- `category is None` or `category == ""`
- `category not in canonical_7_categories`
- `"synapse_weight" not in enabled_targets`
- `current_value` 제공 시 범위 [0.1, 1.0] 초과

### Store Isolation

`SynapseStore`, `SynapseState`, `WeightUpdatePolicy` import/call 0건.

`current_value`는 외부 주입 (`Mapping[str, float] | None`).
STEP 2는 실제 state를 읽거나 쓰지 않는다.

### Source Aggregation — 금지

source별 proposal을 병합하지 않는다.
Mock과 Heuristic 각각 별도 `RPEProposal`을 생성한다.
aggregation 정책은 STEP 6 측정 이후 결정한다.

### Rollback ID

`rollback_id = str(uuid.uuid4())` — 생성만.
`rollback()` 함수는 STEP 3+ active mutation 시점에 구현한다.

### API

```
DopamineRPE.observe(context) -> list[RPEDecision]   # STEP 1, 의미 변경 없음
DopamineRPE.dry_run(context, current_values) -> list[RPEProposal]  # STEP 2 신규
```

`DopamineRPE.active()` / `DopamineRPE.apply()` 구현 금지.

### Logging Events

| event | trigger | key payload fields |
|-------|---------|-------------------|
| `rpe.dry_run_proposed` | proposal 생성 성공 | target, target_key, current_value, proposed_delta, proposed_value, rollback_id, applied=False |
| `rpe.dry_run_skipped` | skip | reason, source, category, target |
| `rpe.dry_run_error` | calculator exception | error_type, error |

skip reasons:
- `no_category`, `invalid_category`, `disabled_target`, `invalid_current_value`

### Module Layout

```
app/rpe/
  models.py      # RPEContext, RPEReward, RPEDecision, DryRunConfig, RPEProposal
  calculators.py # SynapseWeightDryRunCalculator (NEW)
  sources.py     # 변경 없음
  dopamine.py    # dry_run() 추가
```

허용 의존성: `app.core.logging`, `app.rpe.*`만.

## Consequences

### Positive

- active mutation 전에 RPE 신호의 delta 분포를 관측 가능.
- `RPEDecision` observe-only invariant 완전 보존.
- `SynapseStore` 의존성 없이 dry-run 가능 (current_value 외부 주입).
- rollback_id가 proposal에 포함되어 STEP 3 추적 기반 마련.
- source별 proposal 분리로 STEP 6 비교 측정 용이.

### Negative

- 시스템 동작 변화 0건 (아직 mutation 없음).
- `current_value` 외부 주입 필요 — caller가 Synapse state를 알아야 함.
- source aggregation 미정으로 동일 context에 proposal이 여러 개 반환됨.

## Resolution Plan

- **STEP 3**: controlled active mutation 준비 (Synapse write path 설계).
- **STEP 4**: IFOM/PFC target 확장 검토.
- **STEP 5**: BasalGanglia/CR 설계.
- **STEP 6**: RPE impact measurement (dry-run distribution → effect size).
- **STEP 7**: Phase 6 closeout, aggregation 정책 결정.

## References

- `app/rpe/models.py` (DryRunConfig, RPEProposal)
- `app/rpe/calculators.py` (SynapseWeightDryRunCalculator)
- `app/rpe/dopamine.py` (dry_run())
- `tests/phase6/test_dry_run_config.py`
- `tests/phase6/test_rpe_proposal.py`
- `tests/phase6/test_synapse_dry_run_calculator.py`
- `tests/phase6/test_dopamine_dry_run.py`
- `tests/phase6/test_dry_run_logging.py`
- `tests/phase6/test_dry_run_isolation.py`
- `tests/phase6/test_dry_run_safety.py`
- ADR-008 (RPE Data Model and Observe-only Mode)
- `docs/handoff/PHASE6_STEP1_CONTEXT.md`
