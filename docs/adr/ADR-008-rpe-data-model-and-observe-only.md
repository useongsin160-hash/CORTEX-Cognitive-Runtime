# ADR-008: RPE Data Model and Observe-only Mode

## Status

Accepted (Phase 6 STEP 1)

## Context

Phase 6는 CORTEX-AEV에 Dopamine RPE (Reward Prediction Error)를 도입한다.
RPE는 향후 다음 대상의 동적 조정 신호로 사용될 수 있다:

- Synapse weight (`WeightUpdatePolicy`)
- IFOM TTL (`adjust_ttl_with_rpe_hook`)
- PFC timeout / confidence (`PFCIntegrationConfig`)
- Tier-1.5 threshold
- Epinephrine threshold

잘못된 학습 신호는 시스템 전체를 오염시키며 회복이 어렵다. 따라서 Phase 6는
관측(observe) → 시뮬레이션(dry-run) → 실제 변경(active)의 3단계로 진행한다.
본 ADR은 그 중 **observe-only 단계**에서의 데이터 모델과 안전장치 슬롯을 확정한다.

## Decision

### 1. RPE Data Model

세 개의 frozen dataclass:

- **RPEContext**: 발생 시점 시스템 snapshot.
  - `trace_id`, `session_id`, `category`, `difficulty`
  - `response_source`, `latency_ms`
  - `error_occurred`, `timeout_occurred`
  - `continuation_bypass`, `pfc_active`, `pfc_cue_type`, `pfc_hint_applied`
  - `extra: tuple[tuple[str, JsonScalar], ...]` — frozen dataclass 안의 dict 내부
    가변성을 피하기 위해 tuple of pairs로 보관.
- **RPEReward**: reward source가 산출한 신호.
  - `expected_reward ∈ [0.0, 1.0]`, `actual_reward ∈ [0.0, 1.0]`, `confidence ∈ [0.0, 1.0]`
  - `prediction_error = clamp(actual_reward - expected_reward, -1.0, +1.0)` (property)
- **RPEDecision**: observe-only RPE 결정 + 안전장치 슬롯.
  - 슬롯: `mode`, `max_delta`, `rollback_id`, `target`, `proposed_delta`,
    `applied`, `session_scope`, `trace_id`
  - STEP 1 invariant (생성자에서 enforce):
    `mode == "observe_only"`, `applied is False`, `target is None`,
    `proposed_delta is None`, `rollback_id is None`, `max_delta >= 0`

### 2. Signal Sources

- **MockRewardSource**: trace_id → (expected, actual) 매핑, `confidence=1.0`.
  테스트와 observe-only DI 용도.
- **HeuristicOutcomeSource**: 약한 관측 신호.
  - 자세한 정책은 §3 참고.
- **CP3RewardSource / UserFeedbackSource**: STEP 1에서 클래스 구현 금지.
  `RPESignalSource` enum에 값으로만 예약.

### 3. Heuristic Weakness Policy

`HeuristicOutcomeSource`는 정답 신호가 아니다. 다음을 강제한다:

- **No auto positive**:
  - cache hit, Tier-1.5 hit → neutral (0.5)
  - no error, no timeout → baseline 유지 (자동 가산 없음)
- **Negative signals**:
  - `error_occurred` → -0.25
  - `timeout_occurred` → -0.25
  - `response_source == "fallback"` → -0.20
- **Weak positive (label-gated)**:
  - `latency_ms < threshold` and not error/timeout → +0.05
  - `extra["expected_behavior_matched"] is True` → +0.10
  - `extra["expected_continuation"] is True` and `continuation_bypass` and
    `pfc_hint_applied` → +0.05
- **Confidence**: 0.3 기본. expected label 존재 시 0.5. **STEP 1에서 0.5 초과 금지.**
- `actual_reward`는 최종적으로 [0.0, 1.0]로 clamp.

### 4. DopamineRPE Observe-only

`app/rpe/dopamine.py::DopamineRPE`:

- 생성자에서 `mode != "observe_only"`이면 `ValueError`.
- `observe(context) -> list[RPEDecision]`:
  - 각 source의 `compute_reward(context)` 호출.
  - 성공 시 `RPEDecision(mode="observe_only", applied=False, ...)` 생성 후 리스트에 추가.
  - SpinalLogger에 `rpe.observed` / `rpe.source_error` 이벤트 기록.
- **mutation 0건**: `app/synapse/weights`, `app/memory/ifom`, `app/routing/pfc`,
  `app/api/routes`, `app/main` 어느 것도 import/호출하지 않는다.
- `asyncio.CancelledError`는 source/logger 양쪽 모두에서 항상 re-raise.
- source 일반 Exception은 `rpe.source_error` 기록 후 다음 source로 계속.
- logger 일반 Exception은 fail-open 처리 (관측이 죽지 않게).

### 5. Module Layout

신규 디렉토리:

```
app/rpe/
  __init__.py
  models.py     # RPEContext, RPEReward, RPEDecision, enums
  sources.py    # RewardSourceProtocol, MockRewardSource, HeuristicOutcomeSource
  dopamine.py   # DopamineRPE
```

허용된 의존성: `app.core.logging`, `app.rpe.*`만. 다른 모든 `app.*` import 금지.
LLM/embedder/legacy import 금지.

### 6. Logging Events

- **rpe.observed**: source당 한 번. payload =
  `{source, expected_reward, actual_reward, prediction_error, confidence,
    category, response_source, pfc_active, continuation_bypass}`
- **rpe.source_error**: source 실패 시. payload =
  `{source_class, error_type, error}`

`module_name = "dopamine_rpe"` 고정.

## Consequences

### Positive

- Mutation 활성화 이전에 RPE 신호를 실제로 관측 가능.
- dry-run/active 단계를 위한 안전장치 슬롯(`max_delta`, `rollback_id`,
  `proposed_delta`, `target`, `applied`)이 데이터 모델에 이미 마련되어
  STEP 2 진입 시 모델 변경 없이 mode 확장만으로 진행 가능.
- Heuristic의 약한 신호 정책이 ADR에 명문화되어 overfitting 위험 완화.
- Phase 5 측정 패턴(`scripts/measure_phase5_step6.py`) 위에 RPE 측정을
  쌓을 수 있는 토대.

### Negative

- STEP 1만으로는 시스템 동작 변화 0건 (mutation 없음).
- Frozen dataclass + tuple-of-pairs 강제로 일부 API가 dict 대비 다소 verbose.
- expected label 없는 환경에서 Heuristic 신호는 baseline 근처에 머무는
  것이 의도된 동작이므로 실효성은 dry-run/active 진입 후에야 확인 가능.

## Resolution Plan

- **STEP 2**: dry-run simulation. mode `"dry_run"` 허용, target/proposed_delta
  계산 시작, applied=False 유지, rollback_id 시뮬레이션.
- **STEP 3+**: controlled active mutation. mode `"active"` 허용 후
  단일 target부터 점진 적용.
- **STEP 6**: RPE effect measurement (Phase 5 3-mode ablation 패턴 계승).
- **STEP 7**: Phase 6 closeout, ADR-005 / ADR-007 full Superseded 판단.

## References

- `app/rpe/__init__.py`, `app/rpe/models.py`, `app/rpe/sources.py`, `app/rpe/dopamine.py`
- `tests/phase6/test_rpe_models.py`, `tests/phase6/test_rpe_sources.py`,
  `tests/phase6/test_rpe_dopamine.py`, `tests/phase6/test_rpe_logging.py`,
  `tests/phase6/test_rpe_isolation.py`, `tests/phase6/test_rpe_safety_slots.py`
- `app/memory/ifom.py::adjust_ttl_with_rpe_hook` (Phase 5 STEP 2 slot)
- `app/synapse/weights.py::WeightUpdatePolicy` (Phase 5 stub)
- `app/routing/pfc.py::PFCIntegrationConfig` (Phase 5 STEP 6 configurable)
- `docs/measurements/phase5_step6_pfc_impact.md`
- `PHASE6_NEXT.md`
