# ADR-005: Planner Intent Category Fallback (Phase 4 임시값)

## Status

**Partial Superseded** (Phase 5 STEP 7, 2026-05-24)

이전 상태:
- **Accepted** (Phase 4 STEP 5.2.5, 2026-05-21)
- **Superseded by Phase 5 PFC** (예정)

현재:
- Phase 5 STEP 3~5에서 PFC가 일부 intent reasoning을 대체했다.
- 하지만 PFC timeout / error / general_fallback 케이스에서는 Planner의
  category fallback이 여전히 안전망으로 기능한다.
- 따라서 본 ADR은 **Partial Superseded**로 유지되며, 완전 Superseded 결정은
  Phase 6 RPE / live 데이터 이후 재검토한다.

---

## Context

Phase 4 STEP 5.2 100쿼리 full regression에서 routed/swarm 경로 28건의
`SwarmTrace.plan_intent`가 전량 "general"로 분류됨.

**원인 분석:**

1. `PlannerAgent._INTENT_PATTERNS` regex가 100쿼리 회귀 시드의 실제 표현과 매칭 실패.
   한국어 시스템 설계 / 분석 / 아키텍처 프롬프트에 대한 regex 커버리지 부족.

2. category fallback 메커니즘 부재. Evaluator가 올바른 category
   (system_design / data_analysis 등)를 반환해도 Planner가 이를 활용하지 않음.

3. Phase 5 PFC가 intent reasoning을 담당할 예정이나 Phase 4 시점에 미구현.

**영향:**
- PlannerAgent가 모든 routed query에 대해 generic 2-step outline만 생성.
- `requires_context` 판단이 부정확 (general → `requires_context=False`).
- `prompt_for_generator`가 [INTENT] general을 포함, Generator에 부정확한 힌트 전달.

---

## Decision

`PlannerAgent.create_pre_plan()`에 `category: str | None = None` 인자를 추가하고
`_CATEGORY_TO_INTENT` 매핑을 도입한다.

### 매핑 규칙

```python
_CATEGORY_TO_INTENT: dict[str, str] = {
    "coding": "code_generation",
    "math_logic": "analysis",
    "data_analysis": "analysis",
    "system_design": "analysis",
    "writing": "creative",
    "game_design": "creative",
    "general": "general",
}
```

### 분류 우선순위

1. **regex 매칭 우선**: 기존 `_INTENT_PATTERNS` regex에 해당 intent가 잡히면 사용.
   category로 덮어쓰지 않음.
2. **category fallback**: regex 결과가 "general"이면 `_CATEGORY_TO_INTENT` 조회.
3. **unknown → general**: category가 None이거나 매핑에 없으면 "general".

### AsyncSwarm 변경

`AsyncSwarm._parallel_context_and_planner()`에서 `task_context.category`를
`create_pre_plan(category=task_context.category)`로 전달한다.

---

## Consequences

### 긍정적

- plan_intent 다변화: general 28/28 → analysis 26 + creative 2 + general 0 (100쿼리 기준)
- PlannerAgent의 generic outline 의존도 감소.
- `requires_context` 판단 정확도 향상 (analysis → `requires_context=True`).
- Phase 5 PFC 도입 전까지 임시 안전망으로 기능.

### 부정적

- category → intent 매핑이 정적 휴리스틱. 도메인 다변화 시 매핑 갱신 필요.
- Phase 5 PFC 도입 후 본 코드는 제거 대상 (dead code 위험).
- 100쿼리 회귀 시드의 카테고리 분포가 매핑 선택에 영향.
  (coding 쿼리가 적어 code_generation=0 관측 — 시드 편향)

---

## Resolution Plan

1. Phase 5에서 PFC GoalStack 기반 intent reasoning 구현 완료 시
   `PlannerAgent._CATEGORY_TO_INTENT` 및 `category` 인자 제거.
2. 본 ADR을 **Superseded by ADR-00X (Phase 5 PFC Intent Reasoning)** 으로 처리.
3. `create_pre_plan(category=...)` 시그니처는 PFC 통합 방식에 따라 유지 또는 제거.

---

## Measurements

| 지표 | 보정 전 (STEP 5.2) | 보정 후 (STEP 5.2.5) |
|------|-------------------|----------------------|
| plan_intent: general | 28/28 | 0/28 |
| plan_intent: analysis | 0 | 26 |
| plan_intent: creative | 0 | 2 |
| plan_intent: code_generation | 0 | 0 |

---

## References

- `tests/phase2/regression_report.md` — Phase 4 STEP 5.2 / 5.2.5 섹션
- `docs/measurements/phase4_step5_2_observations.md` — 관측 4 (resolved)
- `PHASE4_COMPLETE.md` — STEP 5.2.5 항목
- `PHASE5_NEXT.md` — 결정 5 (PlannerAgent 대체 vs 보조)
- `docs/measurements/phase5_step6_pfc_impact.md` — Phase 5 STEP 6 measurement
- `PHASE5_COMPLETE.md` — STEP 7 closeout

---

## Phase 5 STEP 6 Measurement

Phase 5 STEP 6에서 PFC ↔ Planner bounded hint 및 Continuation bypass의 영향을
3-mode ablation (M0/M1/M2)으로 측정했다.

| 지표 | 실측값 |
|------|-------|
| PFC overhead (M0 → M1 avg) | +0.25ms |
| Continuation bypass accuracy | 100% (23/23 expected_bypass=True → swarm) |
| False positive guard | 0 발동 (8/8 차단) |
| Set A 회귀 안정성 | 70% (측정 환경 cache empty 한계 — pytest 1043/1043 별도 확인) |
| M0 → M2 response_source 변화 | 0건 (bypass도 response_source="swarm"으로 동일) |

**해석**:
- PFC가 일부 intent reasoning을 담당 (cue hierarchy → PFCHint → Planner).
- continuation bypass 시 Planner는 forced intent / forced category로 호출.
- 그러나 PFC timeout, PFC error, general_fallback 케이스는 여전히 발생.
- 따라서 `PlannerAgent._CATEGORY_TO_INTENT` 매핑은 안전망으로 유지.

---

## Status Decision (Phase 5 STEP 7)

Phase 5 STEP 7 closeout 결정:

1. PFC가 일부 intent reasoning을 대체했다 — Phase 5 STEP 3~5 구현.
2. 하지만 PFC timeout/error/general_fallback 케이스에서 Planner regex/category fallback이 여전히 필요하다.
3. 따라서 ADR-005는 **Partial Superseded**로 변경한다.
4. 완전 Superseded 여부는 Phase 6 RPE 또는 live 데이터 이후 재검토한다.
5. `_CATEGORY_TO_INTENT` 매핑 및 `category` 인자는 유지한다 (dead code 아님).
