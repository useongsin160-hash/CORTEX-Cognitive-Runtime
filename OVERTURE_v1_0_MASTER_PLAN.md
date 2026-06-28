# CORTEX 5.0 OVERTURE — v1.0 Feature-Complete 무결점 완성 마스터 플랜

> 기준: PROJECT_STATUS.md(commit `2841200`) + IMPLEMENTED_ORGANS_AUDIT.md + Overture 설계 v0.1.
> 스코프 선언: **PROJECT_STATUS §8.2의 "후순위 deferred"는 폐기**. §8.2 항목 전부를 v1.0 범위로 끌어올린다.
> 원칙: 전부 구현 + 측정 검증 + 무결점. "버그만 잡고 기능은 나중" 분할 접근 폐기.

---

## 0. "무결점"의 정의 (먼저 합의할 것)

feature-complete는 "모든 코드가 존재"를 넘어 다음을 전부 만족할 때 무결점이다.

1. 미구현 기능 전부 실구현 (stub·NotImplementedError·모듈 부재 0건).
2. 각 기능이 측정 harness로 검증됨 (특히 RPE/BG/CR은 측정이 완성의 일부 — §4·ADR-014 전제).
3. 정직성 불변식이 코드 레벨에서 깨지지 않음 (캐시·readiness).
4. 재현 가능 빌드 (lock/pin) + 신뢰 가능한 CI (전체 green 재현).
5. 안전 게이트 상태가 측정 근거 위에서 명시적으로 확정됨.

### 0-1. 유일한 미정 변수 — 게이트 모드 (§4 비목표 충돌 지점)

| | 모드 1 (게이트 동결) | 모드 2 (게이트 해제) |
|---|---|---|
| RPE active | 구현+측정 완료, `enabled=False` 동결 | 측정 후 상시 활성 |
| BasalGanglia | 배선+측정 완료, `applied=False` 유지 | 실제 Go/No-Go 결정 적용 |
| CR | 구현+측정 완료, 수동/제한 모드 | 자동 작동 |
| §4 비목표 정합 | 준수 | **위반(설계 문서 수정 필요)** |
| 무결점 성립 조건 | 측정으로 "왜 끄는지" 입증 | 측정으로 "켜도 안전함" 입증 |

→ **이 선택만 정하면 아래 C 단계 완료 기준이 확정된다. 나머지 A·B·D는 모드 무관 공통.**

---

## A. 기반 / 정직성 (게이트 모드 무관, 전부 필수)

| ID | 작업 | 현재 | 무결점 완료 기준 |
|----|------|------|-----------------|
| A1 | 캐시 mode-tagging | 영속 캐시 키에 mode/model 미포함 (live가 mock 답변 hit) | ExactCache·SemanticCache 키/네임스페이스에 `llm_mode`(+model) 포함. mock↔live 교차 hit miss 테스트 통과 |
| A2 | demo readiness 정직화 | `/demo/readiness` live 상태 하드코딩 + 키 env 불일치(`ANTHROPIC_API_KEY` vs `CORTEX_GEMINI_API_KEY`) | readiness가 core 실제 live 상태 반영. 키 presence 점검 env를 슬롯 실사용 env와 일치. 키 값 비노출 |
| A3 | 멀티 슬롯 실구성 | 5칸 전부 동일 Gemini Flash-Lite (tier 차등 무력화) | 최소 LIGHTWEIGHT/STANDARD/DEEP_THINKING 3칸 차등(모델 또는 파라미터). Overture §8.3 "tier 달라지는 데모" 충족. preflight `check_llm_slots.py`로 검증 |
| A4 | 의존성 재현성 | requires-python 상한 없음 + lock 부재 + 실설치 메이저 상회 | lock/pin 도입(또는 정확 핀). requires-python 실 검증 범위로 정합. `apscheduler` 사용처 확정(B4에서 사용 or 제거) |
| A5 | CI 신뢰성 | 전체 1프로세스 네이티브 크래시 + phase2 Windows 파일락 | 전체 green 재현 전략 확정(분할 실행 공식화 또는 크래시 근본 해소). 임베더 싱글톤 fixture scope 점검. phase2 subprocess 파일락 해소 |
| A6 | 리포 위생 | cortex-ui 고아 submodule, chroma_old 잔재, 루트 레거시 test 10개 | 고아 포인터 제거, 잔재 정리, 레거시 test 정식 분리 또는 삭제 |

---

## B. 미구현 기능 실구현 (feature-complete 본체)

| ID | 작업 | 현재 | 무결점 완료 기준 | 의존 |
|----|------|------|-----------------|------|
| B1 | Tier-1.5 실행 | `execute()` → `[TIER-1.5 STUB]` | 실제 Flash diff-edit 구현. cache hit 증강 경로가 stub 문자열 아닌 실 응답 반환 | A1·A3 |
| B2 | Synapse WeightUpdatePolicy | `apply_*_rpe` → NotImplementedError | 실 가중치 업데이트 구현. RPE active mutation 경로와 정합 | — |
| B3 | RPE record 영속화 + aggregation | in-memory dict, "selection NOT aggregation" | DB 스키마로 record 영속. per-trace-target single-apply 불변 유지하며 aggregation 정책 결정·구현 | DB |
| B4 | RPE 자동 rollback scheduler | "Manual rollback only" | apscheduler 기반 timeout rollback 구현(선언된 의존성 실사용). 수동 rollback 불변 보존 | B3 |
| B5 | RPE observe-only 스위치 분리 | `enabled` 단일 → False면 observe도 차단 | `observe_enabled`/`active_enabled` 분리. observe를 production에서 독립 가동 가능(5.3 telemetry 전제) | — |
| B6 | 3-mode ablation 측정 harness | 미구현 (STEP 6) | observe/dry_run/active 3모드 측정. BG agreement rate, 카테고리별 차이 산출. C 단계 결정의 데이터 근거 | B5·A3 |
| B7 | BasalGanglia production 배선 | advisor 구현됨, main/routes import 0건 | routes에 연결. applied 처리는 게이트 모드에 따름(C). AST import 격리 불변 재검토 | B6 |
| B8 | Conflict Resolution 구현 | 코드 0, ADR-014 Deferred | PFC/LC/Synapse 충돌 탐지 → 측정 → 해결 전략 결정(ADR-014 Accepted) → 구현 → 검증 | B6 |
| B9 | GlymphaticCleaner | 모듈 부재 | maintenance 계층 신규 구현(뇌척수액 정화 = 만료/저신뢰 메모리 정리). IFOM 망각 정책과 역할 경계 명시 | — |

---

## C. 게이트 확정 (0-1 모드 선택에 종속)

| ID | 작업 | 모드 1 완료 기준 | 모드 2 완료 기준 |
|----|------|-----------------|-----------------|
| C1 | RPE active 상태 | 측정 후 `enabled=False` 동결, 근거 문서화 | 측정으로 안전 입증 후 활성, rollback·영속 검증 |
| C2 | BG decision 상태 | `applied=False` 유지, 추천만 노출 | applied=True 결정 적용, agreement rate 검증 |
| C3 | CR 작동 모드 | 수동/제한 모드 동결 | 자동 작동, 충돌 해소율 검증 |
| C4 | §4 비목표 정합 | 설계 문서 그대로 | **Overture 설계 v0.1 §4·§11 수정 필수** |

---

## D. 공개 / Closeout

| ID | 작업 | 무결점 완료 기준 |
|----|------|-----------------|
| D1 | Telemetry UX (O-3) | route/slot/cost/safety/trace 카드. raw trace 접기 |
| D2 | Public hardening (O-4) | input 제한, HTTP rate limit 검증(Glycine 인지비용층과 역할 분리 명시), 에러 메시지 정리, 사용자 테스트 로그 저장. **(해결됨 — A5 잔여 근절):** e5 first-load 의 0xC0000005 는 스레드가 아니라 **mmap 페이지 폴트**(메모리 압박 하 safetensors slice 읽기)가 뿌리로 재진단됨. `app/core/embedder` 에 `disable_mmap=True`(RAM read) 무조건 적용으로 **production lifespan warmup 포함 근원 제거** — 더는 부채 아님 |
| D3 | 문서 동기화 | v0.7 설계도에 D1~D5 불일치 반영. Overture 설계 §5.3 로드맵 문구 정정(observe telemetry는 B5 후 가능). honest implementation table 갱신 |
| D4 | Closeout (O-5) | 전체 회귀 green 재현, demo smoke, live 20-query, README/release note, CORTEX 5.0 tag |

---

## E. 실행 의존 그래프 (한 번에 전부, 단 올바른 순서)

"한 번에 한다"는 스코프를 한 번에 확정한다는 뜻이며, 실행은 아래 의존이 강제하는 순서를 따른다.
순서를 어기면(예: 측정 없이 CR 자동화) 무결점이 성립하지 않는다.

```
[병렬 가능 — 기반]
  A1  A2  A4  A5  A6        (서로 독립)
  A3 ─────────────┐         (멀티슬롯: 측정·Tier-1.5의 전제)
                  │
[기능 — 독립 선행]         │
  B2  B5  B9                │
                  │        │
[측정 게이트]      ▼        ▼
  B5 → B6 ◄── A3            (3-mode 측정은 observe스위치+멀티슬롯 후)
  B3 → B4                   (rollback은 영속화 후)
  A1·A3 → B1                (Tier-1.5 실행)
        │
        ▼
[측정 종속]
  B6 → B7 (BG 배선)
  B6 → B8 (CR: 측정→ADR-014 결정→구현)
        │
        ▼
[게이트 확정]
  C1 C2 C3 C4   ◄── B6 측정 데이터 필수
        │
        ▼
[공개]
  D1 D2 D3 → D4 (closeout)
```

핵심 강제 순서: **B6(측정) → C(게이트 결정) → B8(CR)**. 이건 ADR-014와 §4가 강제하는 것이지 임의 분할이 아니다.

---

## F. 진행 방식

각 항목은 Claude Code 프롬프트 1개로 발행하며, 프롬프트마다 4단계 파이프라인을 내장한다.
1) 코드 읽고 요구사항·허점 분석 + PLAN 제시(승인 대기) 2) 설계 브리핑 3) 무결성 구현 4) 사이드이펙트·성능 검증.
회귀는 phase 분할 실행. git add 금지, 명시적 파일 선택. 키 값 로그 금지.

발행 순서는 E 그래프를 따른다. A1/A2(정직성)는 이미 프롬프트 발행됨(O-1.5).
다음은 E의 선행 묶음(A3·A4·A5·A6 + B2·B5·B9)부터.
