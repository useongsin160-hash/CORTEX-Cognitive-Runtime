# Architecture Decision Records Index

## Active ADRs

| ID | Title | Status | Phase |
|---|---|---|---|
| ADR-001 | SemanticEvaluator latency 재산정 | Accepted | Phase 3 STEP 2 |
| ADR-002 | Phase 4 임베딩 공유 파이프라인 | Partial | Phase 3 STEP 2 / Phase 4 STEP 3.3b |
| ADR-003 | 에피네프린 threshold 임시성 | Accepted | Phase 3 STEP 3.2 |
| ADR-004 | Context Agent ↔ Synapse 인터페이스 규약 | Accepted | Phase 4 STEP 2 |
| ADR-005 | Planner intent category fallback | **Partial Superseded** (Phase 5 STEP 7) | Phase 4 STEP 5.2.5 |
| ADR-006 | IFOM TTL + Status-based Forgetting Policy | Accepted | Phase 5 STEP 2 |
| ADR-007 | Multilingual Cue Support | Proposed | Phase 5 STEP 7 (Resolution: Phase 6 or Later) |
| ADR-008 | RPE Data Model and Observe-only Mode | Accepted | Phase 6 STEP 1 |
| ADR-009 | RPE Dry-run Simulation | Accepted | Phase 6 STEP 2 |
| ADR-010 | RPE Active Mutation Service (STEP 3.1) | Accepted | Phase 6 STEP 3.1 |
| ADR-011 | RPE Active Mutation Pipeline Integration (STEP 3.2) | Accepted | Phase 6 STEP 3.2 |
| ADR-012 | RPE IFOM TTL Target Extension (STEP 4) | Accepted | Phase 6 STEP 4 |
| ADR-013 | BasalGanglia Advisor (STEP 5.1) | **Accepted** (isolation table partially superseded by ADR-015) | Phase 6 STEP 5.1 |
| ADR-014 | Conflict Resolution — Deferred | Proposed / Deferred | Phase 6 Closeout |
| ADR-015 | BasalGanglia Production Wiring (B7) | Accepted | OVERTURE B7 |
| ADR-016 | WeightUpdatePolicy 死 stub 제거 (B2) | Accepted | OVERTURE B2 |
| ADR-017 | Crossroad Reasoning (B8) | Accepted | OVERTURE B8 |

## 작성 규칙

새 ADR 작성 시:
- 파일명: `ADR-{번호}-{kebab-case-title}.md`
- 섹션: Status / Context / Decision / Consequences / References
- 작성 후 본 INDEX.md에 등록

## Status 정의

- **Proposed**: 결정됐으나 아직 구현 안 됨 (Phase 도래 시 발동)
- **Partial**: 일부 구현됨, 전면 구현은 이후 Phase
- **Accepted**: 결정 + 구현 완료
- **Superseded**: 후속 ADR에 의해 대체됨 (대체 ADR 번호 명시)
- **Deprecated**: 더 이상 유효하지 않음

## Status 변경 이력

### 2026-05-21 (Phase 4 closeout — STEP 5.3)

- **ADR-002**: Proposed → **Partial**
  - Phase 4 STEP 1: `QueryFeatures.embedding` 슬롯 도입
  - Phase 4 STEP 3.3b: Evaluator 계산 embedding을 Context Agent 재사용 (재계산 0회)
  - 전면 임베딩 공유 파이프라인 결정은 Phase 5 이후

- **ADR-004**: Proposed → **Accepted**
  - Phase 4 STEP 2: ContextAgent ↔ Synapse 인터페이스 규약 4단계 구현 완료
  - SynapseStore 직접 접근 0건 검증 (`test_synapse_no_direct_access.py`)

- **ADR-005**: 신규 **Accepted** (Phase 5에서 Superseded 예정)
  - Phase 4 STEP 5.2.5: PlannerAgent intent category fallback 임시값
  - plan_intent general 28/28 → 0/28 즉시 해소
  - Phase 5 PFC GoalStack 기반 reasoning 도입 시 Superseded 처리 예정

### 2026-05-22 (Phase 5 STEP 2)

- **ADR-006**: 신규 **Accepted**
  - Phase 5 STEP 2: IFOM TTL + status-based forgetting policy 구현
  - active/paused 60분, completed 10분, low priority 5분
  - completed TTL 초과 → 즉시 제거 / active/paused/low priority → expired 전환
  - Phase 6 RPE hook slot (adjust_ttl_with_rpe_hook) no-op으로 마련

### 2026-05-24 (Phase 5 STEP 7 closeout)

- **ADR-005**: Accepted → **Partial Superseded**
  - Phase 5 STEP 3~5에서 PFC가 일부 intent reasoning을 대체했다.
  - 하지만 PFC timeout/error/general_fallback 케이스에서 Planner regex/category
    fallback이 여전히 안전망으로 기능한다.
  - 완전 Superseded 여부는 Phase 6 RPE 또는 live 데이터 이후 재검토.
  - Measurement 근거: docs/measurements/phase5_step6_pfc_impact.md
    (PFC overhead +0.25ms, bypass accuracy 100%, false positive 0/8)

- **ADR-007**: 신규 **Proposed**
  - 한/영 cue만 지원하는 현재 cue_classifier의 다국어 확장 검토.
  - Phase 5에서는 한/영만 유지. Phase 6 또는 별도 Phase에서 Option A/B/C 비교 후 결정.
  - 결정 시 본 ADR을 Accepted/Rejected/Superseded 중 하나로 갱신.

### 2026-05-24 (Phase 6 STEP 1)

- **ADR-008**: 신규 **Accepted**
  - Phase 6 STEP 1: RPE 데이터 모델 + DopamineRPE observe-only 구현.
  - RPEContext / RPEReward / RPEDecision frozen dataclass.
  - MockRewardSource + HeuristicOutcomeSource (CP3/user_feedback은 슬롯만).
  - mode="observe_only" 강제, applied/target/proposed_delta/rollback_id 비활성화.
  - Heuristic은 약한 신호: cache hit / Tier-1.5 / no-error를 자동 positive로 취급하지 않음.
  - dry-run은 STEP 2, active mutation은 STEP 3+.

### 2026-05-25 (Phase 6 STEP 2)

- **ADR-009**: 신규 **Accepted**
  - Phase 6 STEP 2: RPE dry-run simulation (Synapse weight only).
  - RPEDecision observe-only invariant 완전 유지 — dry-run 결과는 RPEProposal 별도 모델.
  - delta = clamp(pe × conf × max_delta, -max, +max).
  - SynapseStore/WeightUpdatePolicy import 0건. current_value 외부 주입.
  - source별 proposal 분리 (aggregation 금지).
  - rollback_id uuid4 생성만, rollback 실행은 STEP 3+.
  - active mutation은 STEP 3+.

### 2026-05-25 (Phase 6 STEP 3.1)

- **ADR-010**: 신규 **Accepted**
  - Phase 6 STEP 3.1: RPEMutationService 단독 구현 (Synapse weight active mutation).
  - ActiveMutationConfig: disabled-by-default, threshold (conf>=0.5, |pe|>=0.3).
  - RPEMutationRecord: pre/post weight + rollback metadata.
  - SynapseWeightMutator + Protocol + InMemory store + production adapter.
  - per-trace-target single-apply rule (selection, NOT aggregation).
  - Internal per-key asyncio.Lock registry (LockManager 수정 0건).
  - current_values는 stale hint — mutator가 lock 안에서 store 재읽기.
  - Manual rollback only. 자동 scheduler 구현 0건.
  - Production pipeline 통합은 STEP 3.2 영역 (routes/swarm/main 변경 0건).

### 2026-05-25 (Phase 6 STEP 3.2)

- **ADR-011**: 신규 **Accepted**
  - Phase 6 STEP 3.2: RPEMutationPipelineWrapper 로 production pipeline 통합.
  - RPEPipelineSnapshot: frozen snapshot of pipeline state for RPE context.
  - routes.py에서 `state.async_swarm.execute()` → `state.rpe_pipeline.execute()` 대체.
  - main.py에 RPE DI 추가 (enabled=False 기본값).
  - Background task fire-and-forget (fail-open, CancelledError re-raise).
  - swarm.py 변경 0건. QueryResponse/SwarmResult 스키마 변경 0건.
  - PFC state 캡처는 STEP 3.3+ extension slot.

### 2026-05-26 (Phase 6 STEP 5.1)

- **ADR-013**: 신규 **Accepted**
  - Phase 6 STEP 5.1: BasalGanglia Advisor — read-only / recommendation-only.
  - `app/basal_ganglia/` 신규 (models, policies, advisor).
  - `ActionSelectionDecision.applied=False` 강제 (validation).
  - tuple-of-pairs snapshot (synapse_weights, ifom_ttl_overrides, metadata).
  - 점수: `0.4·synapse + 0.3·pfc + 0.05·rpe_balance + 0.1·lc_caution_bonus`.
  - RPE count는 raw가 아닌 정규화된 balance ∈ [-1, +1]로 변환.
  - Deterministic tie-breaker: score → type priority → id lex.
  - PFC / LC / Swarm / routes / main / RPE pipeline에서 BasalGanglia import 0건.
  - Conflict Resolution은 closeout으로 deferred.
  - STEP 6 측정 결과로 통합 여부 결정.

### 2026-05-26 (Phase 6 Closeout)

- **ADR-014**: 신규 **Proposed / Deferred**
  - Phase 6 Closeout: Conflict Resolution (CR) — 구현 없이 부채로 명시.
  - CR 후보 전략 3종 기록 (Veto / Priority Queue / Overlay).
  - `ActionSelectionDecision.applied=False` invariant이 deferred 기간 동안 safety rail 역할.
  - Activation preconditions: STEP 6 측정 완료 + BG agreement rate 데이터 + 전략 선택.
  - Phase 6 내 BasalGanglia production integration 없음 → CR 실질 충돌 없음.

### 2026-06-23 (OVERTURE B8)

- **ADR-017**: 신규 **Accepted**
  - OVERTURE B8: Crossroad Reasoning(갈림길 추론) — RPE override가 고른 1등 route
    밴드가 막상막하(가중치가 밴드 임계 0.4/0.7의 cr_margin 이내)일 때 안정 모드가
    확률(cr_stable_probability)로 인접(탈락) 밴드를 **background explore** 실행,
    별도 sub-trace로 35칸 학습 공급(exploit 고착 방지).
  - **후보=route 밴드**(플래너 단일 플랜 무변경). 응답은 1등 밴드 것, explore는
    학습 전용. 기존 swarm+RPEDifficultyLearner 재사용(신규 학습 로직 0).
  - **2중 동결**: cr_enabled=False(explore 실행) + difficulty_learning_enabled
    (B13, explore 학습) → C3가 둘 켬. leaf(explore_runner 주입).
  - **ADR-014(arbitration)와 별개 기관** — 덮어쓰지 않고 분리 유지. 탐색 모드는
    PFC 신호 미surface(B7 부채)로 구조만·도달 0.

### 2026-06-23 (OVERTURE B2)

- **ADR-016**: 신규 **Accepted**
  - OVERTURE B2: `app/synapse/weights.py` `WeightUpdatePolicy`(apply_*_rpe →
    NotImplementedError, 고정 델타 +0.15/+0.10/−0.10) **死 stub 제거**.
  - 7칸 SynapseState weight-update의 canonical 구현은 ADR-010
    `RPEMutationService`/`SynapseWeightMutator`(PE×conf×max_delta, active_enabled
    =False 동결) — stub은 그것이 대체한 미배선 placeholder였다.
  - 부활(B)은 B13이 버린 고정 보상 발상의 역행 + 7칸 2중 writer → 폐기로 결정.
  - 정리: stub 테스트 파일 삭제 + RPE 격리 가드 4개 지점/3개 파일
    (`app.synapse.weights`/WeightUpdatePolicy) 死 항목 제거. production 동작 변경
    0(미호출 코드).

### 2026-06-22 (OVERTURE B7)

- **ADR-015**: 신규 **Accepted**
  - OVERTURE B7: BasalGanglia advisor를 production 파이프라인에 단방향 배선
    (main/routes → BG). swarm/pfc/lc/rpe.pipeline 및 BG-leaf 격리는 불변.
  - routes가 ratchet 직후(양 경로) `evaluate()` 호출 → `bg.evaluated` 트레이스만.
    추천은 route_path/tier/answer로 미소비. `applied=False` 2중 잠금 보존.
  - 텔레메트리 전용: TaskContext/QueryResponse/SwarmTrace 스키마 무변경,
    app.state는 `basal_ganglia` 1개만 추가(bg_* 0).
  - 부분 스냅샷 정직 강등: PFC(async)·LC ne_level(float 부재)·RPE history 미가용
    → None/0. 입력 발명 금지(B6 조작 경계). synapse_snapshot만 실 신호.
  - fail-open 래퍼(CancelledError만 re-raise) — 자문 실패가 요청을 깨지 않음.
  - 실제 Go/No-Go 적용(candidate_type↔path 매핑 + applier)은 C2로 이연.
- **ADR-013**: Accepted → isolation table **partially superseded by ADR-015**
  - STEP 5.1의 "main/routes MUST NOT import app.basal_ganglia"를 B7이 단방향
    배선으로 반전. inner-layer/BG-leaf 격리 규칙은 그대로 유효.

### 2026-05-25 (Phase 6 STEP 4)

- **ADR-012**: 신규 **Accepted**
  - Phase 6 STEP 4: RPE IFOM TTL target extension (session/category scoped).
  - IFOMTTLType: "active" / "paused" / "completed" / "low_priority" (4종).
  - IFOMTTLOverrideStore: sync Protocol + InMemory backend.
  - target_key format: `"{ttl_type}:{category}"`, lock_key: `"ifom_ttl:{target_key}"`.
  - IFOMTTLDryRunCalculator: 동일 PE×conf×max_delta 공식, 300s max_delta.
  - IFOMTTLMutator: sync (IFOMPolicy sync 호환). 전역 IFOMConfig 변경 0건.
  - RPEMutationService: ifom_ttl dispatch 추가 (ifom_mutator 선택적 파라미터).
  - DopamineRPE.dry_run: target-agnostic (synapse_weight + ifom_ttl 4종 병렬).
  - IFOMPolicy: ttl_override_resolver Callable 선택적 주입. app.rpe 임포트 0건.
  - DryRunConfig: synapse_weight 필수 제약 제거. ifom_ttl 바운드 추가.
  - RPEProposal: target "ifom_ttl" 허용 (STEP 2 synapse_weight 전용 제약 완화).
  - RPEMutationRecord: target-aware lock_key 검증.
  - RPEMutationPipelineWrapper 변경 0건. routes.py 변경 0건. swarm.py 변경 0건.
