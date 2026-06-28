# CORTEX 5.0 OVERTURE — 통계 (코드 변화량 · 커밋 · 테스트)

> [CORTEX_5_0_OVERTURE_ARCHITECTURE.md](CORTEX_5_0_OVERTURE_ARCHITECTURE.md)의 통계 부록(§6).
> 모든 수치는 git·pytest에서 직접 실측한 값이다(추측 없음). 기준 커밋 `58f8fc0`(2026-06-27),
> 인터프리터 `.venv` Python 3.11.9.

---

## §6.1 코드 변화량 (AEV 대비)

⚠️ **기준점 두 갈래(정직 표기):**
- **A — 설계도 v0.7 closeout** `050ec05` (2026-05-26)
- **B — OVERTURE A-트랙 직전** `2841200` (2026-06-02; A1 `52ae395`의 부모)

두 커밋 사이에는 9개의 전이 커밋(슬롯 레지스트리 V1~V4, 데모 D1, 감사 문서, live-answer)이 있어
구분한다.

| 기준 | 커밋 수 | 전체 diff | `app/` (실 코드) | `tests/` |
|---|---|---|---|---|
| **A: v0.7 `050ec05`→HEAD** | 44 | 168 파일, +58,205 / −1,160 | 52 파일, +4,667 / −294 | 85 파일, +6,569 / −658 |
| **B: OVERTURE-A `2841200`→HEAD** | 35 | 141 파일, +54,236 / −978 | 44 파일, **+3,882 / −231** | 76 파일, +5,336 / −651 |

⚠️ **삽입 라인 인플레이션:** 전체 diff의 +54k~+58k에는 **생성된 측정 JSON 산출물**이 포함된다
(`bg_redesign.json` ~13,400줄, `bg_full_input.json` 등). 따라서 **실 코드 변화의 정직한 지표는
`app/` diff(기준 B 기준 +3,882 / −231 라인)**이다.

전체 누적: **121 커밋**(저장소 최초 ~ HEAD `58f8fc0`).

## §6.2 트랙별 주요 커밋

OVERTURE 작업은 A(기반/정직성) · B(미구현 기능 실구현) · C(게이트 확정) 트랙으로 진행되었다.

**A — 기반 / 정직성**
| ID | 내용 | 커밋 |
|---|---|---|
| A1 | 캐시 mode/slot 네임스페이싱 | `52ae395` |
| A2 | demo readiness 정직화 | `4c02ef2` · `47f3fc2` |
| A3 | 멀티 벤더 슬롯 차등화 | `36e72a8` |
| A4 | 의존성 lock/pin SSOT | `b3229b4` |
| A5 | CI 1프로세스 green | `b8676d6` · `2e6071b` |

**B — 미구현 기능 실구현**
| ID | 내용 | 커밋 |
|---|---|---|
| B5 | RPE observe/active 분리 | `4773acb` |
| B6 | 35칸 학습 궤적 측정 harness | `9149c19` → `a1022a1` |
| B12 | 난이도 5단계 + 난이도→tier 1:1 | `5bac013` |
| B11 | RPE 난이도 학습 + 생체 라우팅 + 래칫 + decay (S1~S5) | `e3f0ee4` … `e1e561f` |
| B13 | 보상 소스 강화 + confidence 복원 | `754f729` · `f384e5c` · `f7ed573` |
| B3 | RPE record + EMA 프리셋 영속 | `3a662cd` · `d1c5278` |
| B4 | RPE 자동 rollback scheduler | `76ee5ab` |
| B7 | BasalGanglia advisor 배선 | `f1a98ce` |
| B2 | WeightUpdatePolicy 死 stub 폐기 | `70313f8` |
| B9 | GlymphaticCleaner | `74d19f7` |
| B1 | Tier-1.5 diff-edit 실행 | `d4cc4a9` |
| B8 | Crossroad Reasoning | `afe0992` |
| B10 | PFC/LC/RPE 신호 배관 + CR 탐색 모드 | `a9c0e23` |

**C — 게이트 확정**
| ID | 내용 | 커밋 |
|---|---|---|
| C1 | RPE 35칸 학습 활성화 | `10f2088` |
| C3 | Crossroad Reasoning explore 활성화 | `876c09b` |
| — | BasalGanglia 의사결정 재설계(C2 선행) | `48cb162` |
| C2 | BasalGanglia apply 활성화(승급-전용) | `58f8fc0` |

## §6.3 테스트 — 수 / 종류 / 방식

**총 2,046 collected** (`pytest --collect-only`, 1프로세스). 디렉토리별(합 = 2,046 정확 일치):

| 디렉토리 | 수 | 디렉토리 | 수 |
|---|---|---|---|
| `tests/phase6` | 887 | `tests/core` | 44 |
| `tests/phase5` | 562 | `tests/phase3_5` | 31 |
| `tests/phase4` | 292 | `tests/execution` | 25 |
| `tests/phase3` | 96 | `tests/demo_backend` | 17 |
| `tests/phase2` | 82 | `tests/phase1` | 10 |

**종류 (코드 실재):**
- **단위(unit):** 기관별 로직 검증(예: `test_basal_ganglia_policy`, `test_skip_router`)
- **통합/스모크:** `app_client` fixture로 앱 기동 후 경로 검증(`test_routes_smoke_20`, phase4/5 호환)
- **격리(isolation, AST import):** forbidden import를 정적으로 차단(`test_rpe_isolation`,
  `test_basal_ganglia_isolation`)
- **회귀(regression):** `test_regression_100`
- **측정 harness:** 게이트 결정 근거 산출(`test_3mode_ablation_harness` 등) — **"신호 발명 0 ·
  결정론"을 단언**
- **배선(wiring):** 기관의 production 배선 검증(`test_basal_ganglia_wiring` — B7 관측 + C2 apply)
- **수명주기(lifespan):** `test_rpe_pipeline_lifespan`

**방식 (운영):**
- 1프로세스 전체 회귀(`pytest tests/`), lock `.venv` Python 3.11.9
- 8GB 호스트에서 e5 임베더 메모리 플레이키를 회피하기 위해 메모리 확보 후 실행
- 격리: 세션 단위 app + ephemeral ChromaDB + main-thread 임베더(A5)
- 최근 전체 회귀 결과: **2,046 passed**

**정직성 보증:** 측정 harness는 압축 지표(예: agreement rate)를 산출하지 않고 raw 관측만 기록하며,
결정론(동일 입력 → 동일 출력)과 신호 발명 0을 코드 레벨 단언으로 강제한다.
