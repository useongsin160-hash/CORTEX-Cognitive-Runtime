# OVERTURE version history

> CORTEX-AEV Core **v0.7** → **CORTEX 5.0 OVERTURE (v1.0 target)** 작업 현황 로그.
> 정본 계획: [OVERTURE_v1_0_MASTER_PLAN.md](OVERTURE_v1_0_MASTER_PLAN.md). AEV v0.7 정본:
> [CORTEX_AEV_현행_구현기준_설계도_v0_7.md](docs/legacy/CORTEX_AEV_현행_구현기준_설계도_v0_7.md).
>
> **유지보수 규약:** OVERTURE 작업(A/B/C/D 항목) 하나가 끝날 때마다 이 문서에 엔트리 1개를
> append 하고 §1 표와 헤더 날짜를 갱신한다(§3 템플릿). 커밋 전 작업을 "완료"로 적지 않는다 —
> 정직성 불변식("live가 아니면 live처럼 보이게 하지 않는다")의 문서판이다.
>
> **마지막 갱신:** 2026-06-28 — **D-prep 정합 감사** 완료(커밋 `7523b84`). 공개 직전 정본 §1 vs app/ 전수 대조로 누락 발굴 + 부채 일부 코드 보수(사용자 범위 선택). ① 누락 LIVE 기관 정본화: Neuromodulators(Epinephrine·Norepinephrine·Glycine)·Continuation·CueClassifier·CentroidStore(§1.3). ② **no-GC 무한 성장 해소**: SynapseStore·difficulty 스토어에 bounded LRU(evict→preset graceful). ③ **CR PFC-directed explore 가동+확장**: B10이 이미 배선·도달 가능(stale 주석이 "미가동" 오기)이었고, C4가 신호를 임의 cue 저신뢰(conf<0.6)로 확장(경계 매치 포함, 발명 0). §1.4 부채 정합(ADR-014·다국어·PLC·pfc_stub 추가). 무거운 재설계(NE 연속값 등)는 §1.5 공개-후 트랙. 회귀 **2051 green**. **OVERTURE v1.0 — 공개 직전 정합 완료.**

---

## 0. OVERTURE란 — AEV v0.7과 무엇이 다른가

CORTEX-AEV **v0.7**은 Phase 6 closeout 기준 인지 미들웨어 정본이다(Tier Slot Registry,
RPE observe/dry-run/active, BasalGanglia advisor, Async Swarm, routing/LC, 영속 캐시
ExactCache·SemanticCache, demo_backend D1). 대부분의 인지기관이 구현됐으나 일부는
**read-only / gated / stub** 상태이고, 정직성·측정·재현성은 부분적이었다.

**OVERTURE(CORTEX 5.0)**는 이를 **v1.0 feature-complete "무결점"**으로 끌어올리는 업그레이드
트랙이다. 무결점의 정의(master plan §0):

1. 미구현 stub·NotImplementedError·모듈 부재 **0건**.
2. 각 기능이 **측정 harness**로 검증됨(특히 RPE/BG/CR).
3. **정직성 불변식이 코드 레벨에서 강제**됨(캐시·readiness).
4. 재현 가능 빌드(lock/pin) + 신뢰 가능한 CI(전체 green 재현).
5. 안전 게이트 상태가 **측정 근거 위에서** 명시적으로 확정됨.

### 작업 분류 (master plan A~D)

| 트랙 | 의미 | 항목 |
|---|---|---|
| **A** | 기반 / 정직성 (게이트 모드 무관, 전부 필수) | A1~A6 |
| **B** | 미구현 기능 실구현 (feature-complete 본체) | B1~B9 |
| **C** | 게이트 확정 (RPE/BG/CR 동결 vs 해제) | C1~C4 |
| **D** | 공개 / Closeout | D1~D4 |

지금까지(2026-06-12)는 **A 트랙(기반/정직성)**이 진행됐다. A 트랙의 공통 불변식:

> **"live가 아니면 live처럼 보이게 하지 않는다."**
> 시스템의 외부 신호(캐시 hit, readiness, 데모 표시)가 실제 런타임 상태(mock/live,
> 슬롯 키 구비, 벤더 구성)와 어긋나지 않도록 코드 레벨에서 강제한다.

---

## 1. 진행 현황 한눈에

| ID | 작업 | 상태 | 커밋 | 일자 |
|----|------|------|------|------|
| **A1** | 캐시 mode/slot 네임스페이싱 (영속 캐시 정직화) | ✅ 완료 | `52ae395` | 2026-06-10 |
| **A2** | demo readiness 정직화 + core 상태 노출 | ✅ 완료 | `4c02ef2` + `47f3fc2` | 2026-06-12 |
| **A3** | 멀티 벤더 슬롯 차등화 (벤더 중립 디스패치 입증) | ✅ 완료 | `36e72a8` | 2026-06-12 |
| **A4** | 의존성 lock/pin + SSOT 일원화 | ✅ 완료 | `b3229b4` | 2026-06-13 |
| **A5** | CI 신뢰성 — 전체 테스트 1프로세스 green 근본 해소 | ✅ 완료 | `b8676d6` + `2e6071b` | 2026-06-16 |
| A6 | 리포 위생 (고아 submodule 등) | ⬜ 미착수 | — | — |
| **B5** | RPE observe-only 스위치 분리 (observe/active 독립 게이트) | ✅ 완료 | `4773acb` | 2026-06-17 |
| **B6** | 측정 harness — 7칸 t=0 단발 → 35칸 학습 궤적(faithful+latent 2-pass, 중립성 단언, 3-mode 격리, BG raw) | ✅ 완료 | `9149c19` → `a1022a1` | 2026-06-21 |
| **B12** | 난이도 5단계 정합 복원 + 난이도→tier 1:1 (B11 선행) | ✅ 완료 | `5bac013` | 2026-06-19 |
| **B11** | RPE 난이도 학습(35칸) + 생체 라우팅 + 래칫 + decay(S1~S5) | ✅ 완료 | `e3f0ee4`…`e1e561f` | 2026-06-21 |
| **B13** | 보상 소스 강화 — 관측 가능 성공 신호 복원 + confidence 기관 복원(상한 0.6), active는 C로 동결 | ✅ 완료 | `754f729`·`f384e5c`·`f7ed573` | 2026-06-21 |
| **B3** | RPE record + 35칸 글로벌 EMA 프리셋 aiosqlite 영속(C 하이브리드, 학습 전용 roll-up) | ✅ 완료 | `3a662cd`·`d1c5278` | 2026-06-22 |
| **B4** | RPE 자동 rollback scheduler(AsyncIOScheduler timeout, 수동 보존, apscheduler A4 승격) | ✅ 완료 | `76ee5ab` | 2026-06-22 |
| **B7** | BasalGanglia advisor production 배선 (applied=False 텔레메트리 전용, 단방향 격리, C2가 적용 켬) | ✅ 완료 | `f1a98ce` | 2026-06-22 |
| **B2** | Synapse WeightUpdatePolicy 死 stub 폐기 (apply_*_rpe NotImplementedError 제거 — RPE active mutation[ADR-010]이 canonical 7칸 updater) | ✅ 완료 | `70313f8` | 2026-06-23 |
| **B9** | GlymphaticCleaner — persistent 저장소(ChromaDB 캐시 + RPE record) 주기 나이 청소 (순수 삭제 no-LLM, B4 scheduler 재사용, enabled=False opt-in, 압축 전략 자리만) | ✅ 완료 | `74d19f7` | 2026-06-23 |
| **B1** | Tier-1.5 diff-edit 실행 — stub → 주입 client(LIGHTWEIGHT/Flash 슬롯), 언어중립 prompt, 코드 내 mock 분기 0, LLM 실패 시 cached 폴백 | ✅ 완료 | `d4cc4a9` | 2026-06-23 |
| **B8** | Crossroad Reasoning(갈림길 explore) — 막상막하 route 밴드(임계 5% 근접)에서 인접 밴드 background explore + sub-trace 35칸 학습, 2중 동결(cr_enabled + B13) | ✅ 완료 | `afe0992` | 2026-06-23 |
| B10 | RPE decay(시냅스 시간 감쇠) — **B11 S5에서 라우팅 floor decay로 흡수** | 🟡 부분(라우팅 한정) | `e1e561f` | 2026-06-21 |
| **C1** | RPE difficulty learning 활성화 — Settings 게이트(기본 True, 두 config), 자동 revert 기본(세션 잠정·글로벌 영속), BG/CR 분리 동결 보존 | ✅ 완료 | `10f2088` | 2026-06-23 |
| **C3** | Crossroad Reasoning 활성화 — cr_enabled 기본 True 플립(이미 Settings); C1 learn 게이트 + C3 explore 게이트 동시 가동, 안정 모드만 live, sub-trace 35칸 공급, BG 동결 유지 | ✅ 완료 | `876c09b` | 2026-06-23 |
| **B10 (신호 배관)** | PFC/LC/RPE를 BG에 surface(발명 0) + CR 탐색 모드 활성 — BG 완전 입력화(applied=False 유지), 측정 산출. ⚠️ 마스터 플랜 B10(RPE decay, 위 행)과 라벨만 동일·별개 작업(BG 완전 구현의 선행) | ✅ 완료 | `a9c0e23` | 2026-06-27 |
| **BG 재설계 (C2 선행)** | BG 의사결정 재설계 — 점수식 가산합→compute-demand 매칭(난이도 B12 앵커 + 실 NE/RPE/synapse/PFC 부호화 변조), candidate_type↔route_path 매핑 정의(미소비), 재설계 측정(고난도 강등 378→0·저난도 3 type). 신호 발명 0, applied=False 유지 | ✅ 완료 | `48cb162` | 2026-06-27 |
| **C2** | BG applied 활성화 — 승급-전용(promote-only), ratchet 이후(baseline 우회 0), decision 동기(CR desync 0)+epinephrine 재유도, `bg_apply_enabled`(기본 True), 모델 하드락 보존, BG 승급 ephemeral(학습 floor 미오염) | ✅ 완료 | `58f8fc0` | 2026-06-27 |
| **C4** | 정본 문서 + 레거시 격리 — 현행 ARCHITECTURE/METRICS 정본 신규(실측 데이터), AEV 정본 docs/legacy/ 무수정 격리, ADR·README 보존, similarity test-guard 제외, 회귀 영향 0 | ✅ 완료 | `17c1d54` | 2026-06-27 |
| **D-prep 감사** | 공개 직전 정합 — 누락 LIVE 기관 정본화(Neuromodulators 3·Continuation·Cue·Centroid) + no-GC bounded LRU(Synapse·difficulty 스토어) + CR explore 가동·신호 확장(conf<0.6) + §1.4 부채 정합. 무거운 재설계는 §1.5 공개-후 | ✅ 완료 | `7523b84` | 2026-06-28 |
| D1~D4 | 공개 / Closeout | ⬜ 미착수 | — | — |

**상태 범례:** ✅ 커밋 완료 · 🟡 진행 중(분석/PLAN/구현, 미커밋) · ⬜ 미착수.

---

## 2. 작업별 상세

각 엔트리는 **추가된 것 / 바뀐 것 / 개편된 것**을 AEV v0.7 대비로 기술한다.

### A1 — 캐시 mode/slot 네임스페이싱 (영속 캐시 정직화)

- **상태:** ✅ 완료 · **커밋:** `52ae395` · **일자:** 2026-06-10
- **커밋 제목:** `fix(cache): namespace persistent caches by llm_mode and slot fingerprint to prevent cross-hit`

**문제 (v0.7):** 영속 캐시(ExactCache=SQLite, SemanticCache=ChromaDB)가 캐시 키에
`llm_mode`/슬롯 식별자를 담지 않아, **live 모드가 mock 시대에 생성된 답변을 hit**할 수 있는
정직성 버그가 있었다(키가 prompt만 반영).

**추가된 것**
- `app/ingress/cache_key.py` — 캐시 네임스페이스 키의 **단일 정규화 모듈**(신규). 스킴
  `cortex-cache-v2 = cache_schema | cache_kind | llm_mode | slot_fp | model_id | prompt`.
- `slot_fingerprint(tier_name, protocol, base_url, model)` — public-safe 슬롯 지문 헬퍼.
  **api_key 값·api_key_env 이름은 시그니처에 구조적으로 부재** → secret이 키/지문에 들어갈 수 없음.
- `tests/phase2/test_cache_mode_namespacing.py`(신규, ExactCache 8 + SemanticCache 10).

**바뀐 것**
- ExactCache는 정규 sha256을 `prompt_hash`로 사용(DDL 무변경, 마이그레이션 없음).
- SemanticCache는 upsert id에 네임스페이스 포함(mock/live/슬롯별 공존, no overwrite) +
  Chroma `where`($and) 필터로 다른 mode/slot·retrieval corpus·구 엔트리를 검색 단계에서 배제.
- `routes.py` 캐시 read가 `llm_mode`를 네임스페이스에 실어 조회.

**개편된 것**
- 캐시 키 생성이 **단일 경로(cache_key.py)로 통합** — get/put 양쪽이 이 모듈만 거쳐 드리프트 차단.
- 비-mock(live) write는 resolved slot/model 없이는 거부(`CachePolicyError`) — **fingerprint seam**
  도입. production read는 LC tier 선택 이전이라 `__unresolved__`로 조회(read-side hardening).
  **write-back은 A1 범위 밖**(seam만 준비; 후속 B 계열).

**정직성·스코프:** TaskContext/SwarmTrace/QueryResponse 무변경. 기본 `llm_mode=mock` 보존.
구 영속 엔트리는 전부 graceful miss(콜드스타트 1회, 능동 마이그레이션 없음). RPE/BG/CR/routing/
swarm·public claims 무수정.

**테스트(phase 분할):** tests/core 22 · 신규 namespacing 18 · test_caches 6 · phase2 pipeline 9 ·
인접 route-double 스위트 다수 — all passed.

---

### A2 — demo readiness 정직화 + core 상태 노출

- **상태:** ✅ 완료 · **커밋:** `4c02ef2`(core) + `47f3fc2`(demo) · **일자:** 2026-06-12
- **커밋 제목:**
  - `feat(core): expose llm_mode and slots_ready on health (read-only, non-breaking, log-free)`
  - `fix(demo): proxy core live/slots state in readiness; drop vendor-specific key checks`

**문제 (v0.7):** `/demo/readiness`가 `llm_live_enabled=False`·`can_run_live_llm=False`·
`demo_mode="stub"`를 **하드코딩**(core가 실제 live여도 영구 non-live 보고)하고, 키 presence를
`ANTHROPIC_API_KEY`로 직접 검사했으나 실제 슬롯은 `CORTEX_GEMINI_API_KEY`를 써 **env 불일치**
(PROJECT_STATUS §7.3). → 거짓 not-ready + 벤더 종속 신호.

**추가된 것**
- core `HealthResponse`에 read-only 필드 `llm_mode`·`slots_ready`(additive, 비파괴).
- `slot_registry.evaluate_slot()` + `slots_ready()` — 슬롯 preflight 집계(strict AND, 5칸 전부
  OK/OK_NO_AUTH일 때만 True).
- 신규 테스트: `tests/core/test_slots_ready.py`, `tests/core/test_health_status.py`,
  `tests/demo_backend/test_readiness.py`.

**바뀐 것**
- `/demo/readiness`가 하드코딩 → **core `/health` 중계**(`llm_live_enabled = llm_mode=="live"`,
  `can_run_live_llm = live AND slots_ready`, `demo_mode` 파생).
- core `/health` 핸들러가 `llm_mode + slots_ready`를 반환(SpinalLogger 미호출 = **log-free**,
  readiness 폴링에도 로그 비오염).
- `check_llm_slots.py`가 평가 로직을 `slot_registry`로 **위임**.

**개편된 것**
- 슬롯 키 검증의 **SSOT를 `slot_registry`로 승격**(스크립트와 런타임 검증 이원화 제거).
- demo가 자체 키/벤더 판단 폐기: `_llm_key_present`·`LLM_KEY_ENV`·`LLM_KEY_PLACEHOLDER` 제거.
  readiness 필드 `llm_key_present` → **`slots_ready`로 개명**(벤더 중립, core 필드명 일치).

**정직성·스코프:** 거짓 ready/거짓 not-ready 제거. core 미도달 시 graceful 200 not-ready(500·거짓
ready 금지). 키 값·env 이름·벤더명 응답/로그 미노출. 기본 mock·`require_live` 게이트 보존.

**테스트(phase 분할):** tests/core 34 · tests/demo_backend 17 · phase4 response_schema 17 ·
route smoke 2 — all passed.

---

### A3 — 멀티 벤더 슬롯 차등화 (벤더 중립 디스패치 입증)

- **상태:** ✅ 완료 · **커밋:** `36e72a8` · **일자:** 2026-06-12
- **커밋 제목:** `feat(slots): multi-vendor example template + adapter-routing tests proving vendor-neutral dispatch`

**문제 (v0.7):** 실파일 `config/tier_slots.json`이 5칸 전부 동일 `google`/Gemini Flash-Lite라
**tier 차등이 0**이고 **벤더 중립성이 시연되지 않았다**(Overture §8.3 "tier 달라지는 데모" 미충족).

**분석 결론:** 디스패치(`ADAPTERS[slot.protocol]`)·`UnsupportedProtocolError`·`slots_ready`의
미지원 protocol 거름은 **이미 올바르게 구현**돼 있었다. 따라서 A3는 구조 결함 수정이 아니라
**config + 테스트로 입증·고정**하는 작업이며 **app/ 코드는 0 변경**이다.

**추가된 것**
- `tests/execution/test_multi_vendor_routing.py` — 단일 멀티벤더 config에서 protocol별 올바른
  실 어댑터 라우팅 입증(MockTransport, 네트워크 0): `google→:generateContent`,
  `anthropic→/v1/messages`, `openai_compatible→/chat/completions`. + H3 drift 가드
  (`KNOWN_PROTOCOLS == set(ADAPTERS.keys())`) + AST 정적 검사(디스패치 벤더 `==` 분기 0).
- `tests/core/test_slot_differentiation.py` — `slots_ready` 멀티벤더(키 이름 하드코딩 없음 ·
  미지원 protocol 거름) + `slot_fingerprint` 차등 민감도·슬롯별 distinct(A1 seam 실재화,
  캐시 로직 무변경) + 갱신 템플릿 멀티벤더·INCOMPLETE/NO-GO 단언.

**바뀐 것**
- `config/tier_slots.example.json` — 3종 protocol 혼합 · 슬롯별 distinct `api_key_env` ·
  tier별 `model` 차등으로 갱신.

**개편된 것** — 없음(코드 구조 무변경). H3 drift(KNOWN_PROTOCOLS ↔ ADAPTERS 이원 하드코딩)는
가드 테스트로 고정.

**결정(승인됨):** ① example.json은 `model`만 채우고 `base_url`은 **공백 유지** → preflight가
INCOMPLETE→NO-GO로 "미완성 템플릿"을 정직하게 보고(가짜 base_url로 MISSING_KEY 위장 회피).
② 그 결과 `test_check_llm_slots`·`docs/demo/LIVE_LLM_RUNBOOK.md` **무변경**. ③ 실파일
`tier_slots.json`은 덮어쓰지 않고 운영 결정으로 분리(단일 Gemini 구성 현재 합당).

**정직성·스코프:** 벤더 이름 새 분기 0. `LLMResult`/`generate` 시그니처 · `HealthResponse`/
`SwarmTrace`/`QueryResponse` · 기본 mock · live NO-GO 가드 무변경. 키 값 URL/로그 미노출.

**테스트(phase 분할):** tests/execution 25 · tests/core 44 · phase4 stub 2 · example preflight
INCOMPLETE→NO-GO — all passed.

---

### A4 — 의존성 lock + requires-python 정합 (재현 빌드 바닥)

- **상태:** ✅ 완료 · **커밋:** `b3229b4` · **일자:** 2026-06-13
- **커밋 제목:** `chore(deps): pin direct deps as pyproject SSOT (drop requirements.txt), align requires-python`

**문제 (v0.7):** 선언 범위와 실설치 버전이 메이저 격차(chromadb `>=0.4.24`→`1.5.9`,
sentence-transformers `>=2.7`→`5.5.1`, pytest-asyncio `>=0.23`→`1.4.0`)였고, pyproject 와
구 CORTEX-3.0 `requirements.txt` **두 파일이 하한까지 불일치**해 재현 빌드가 불가능했다.

**바뀐 것**
- `pyproject.toml [project.dependencies]` 8개를 lock 인터프리터(.venv **Python 3.11.9**)
  실설치 버전으로 **`==` 핀**. 버전 up/down 0(도는 그대로 고정). transitive 는 핀하지 않음.
- `httpx` 를 `[dev]`→`[project.dependencies]`로 이동·핀 — 런타임 의존성(adapters·demo 프록시)
  오배치 교정.
- `requires-python = ">=3.11,<3.15"` — 하한 3.11(테스트 환경), 상한=미검증 메이저 캡.
  실측 검증은 **3.11.9 뿐**, 3.12~3.14 는 허용하되 미검증.

**추가된 것**
- `[project.optional-dependencies] legacy` 그룹 — `google-genai`·`tiktoken`·`apscheduler` 를
  `==` 핀하되 **현행 핀 세트에서 강등**(셋 다 `legacy/cortex_3_1` 전용, app/ import 0).
  `apscheduler` 주석에 "B4 에서 현행 의존성 승격 예정". 실제 제거는 A6.

**개편된 것**
- **`requirements.txt` 삭제 → pyproject 단일 진실 공급원(SSOT)**. 두 파일 드리프트 영구 차단.
  재현 절차는 pyproject `[project]` 주석에 명시. OS 마커는 불요(OS 전용 직접 의존성 없음;
  colorama 등은 transitive).

**정직성·스코프:** 의존성 메타데이터만. app/ 런타임·기본 mock·live NO-GO 가드·스키마 무변경.
삭제된 requirements.txt 참조(README.md:74 등)는 스코프 가드상 미수정 — stale flag(후속 docs).

**테스트:** `pip install --dry-run` 14개 핀 전부 "already satisfied"(충돌 0·비파괴) ·
tests/core 44 · tests/execution 25 — all passed.

---

### A5 — 전체 테스트 1프로세스 green 근본 해소 (테스트 격리 + 임베더 수명주기)

- **상태:** ✅ 완료 · **커밋:** `b8676d6`(Layer 1) + `2e6071b`(Layer 2) · **일자:** 2026-06-16
- **커밋 제목:**
  - `fix(runtime): add SemanticCache.close() lifecycle hook + lifespan shutdown call`
  - `test(infra): single-process suite isolation — session app + ephemeral chroma + main-thread embedder`

**문제 (v0.7):** `pytest tests/` 를 **한 프로세스**로 돌리면 세 가지 네이티브 크래시로
중단됐다(분할 실행으로만 green). ① e5 텐서를 `asyncio.to_thread` 워커 스레드에서
materialize → `0xC0000005`. ② 라우트 테스트마다 독립 lifespan + 실 chromadb
PersistentClient 생성 → teardown 크래시(`RustBindingsAPI ... bindings`) + Windows sqlite
파일 락(WinError 32). ③ phase1 회귀 subprocess 가 e5 를 또 로드 → 부모+자식 동시 상주가
호스트 commit 한계(RAM 8GB·가용 commit ~2GB) 초과 `OSError 1455`.

**원칙(2층 분리):** Layer 1 = 실 프로덕션 결함만 고친다. Layer 2 = 고친 코드를 테스트가
DI 로 격리한다. **app/ 에 `if testing/pytest` 환경 감지 분기 0**. 재현으로 (b) "프로덕션
멀티 client 버그" 가설을 기각 — 크래시 뿌리는 "테스트가 격리 없이 다수 client 를 생성·
e5 를 워커 스레드에서 materialize" 였다. 따라서 close() 의 성격은 **크래시 수정이 아니라
리소스 위생 + DI 시드**로 정직하게 명시.

**추가된 것**
- `tests/conftest.py`(신규) — 루트 테스트 인프라. import-time env 리다이렉트(DB/CHROMA →
  세션 tmp; `get_settings()` lru_cache prime 前 적용을 assert 로 검증) · **메인 스레드 선-warm
  shared_embedder** · ephemeral SemanticCache 팩토리(생성 직전 `clear_system_cache` 로 독립
  in-memory system 부여) · 세션 app `app_client`(lifespan 1회) · `HF_DEACTIVATE_ASYNC_LOAD=1`.
- `app/ingress/semantic_cache.py` — `close()`(멱등 핸들 해제 + best-effort system cache 비움).

**바뀐 것**
- `app/main.py` lifespan 종료에서 `semantic_cache.close()` 호출(방어적 getattr).
- 14개 라우트 모듈: `with TestClient(app)`(독립 lifespan) → `app_client` 대여(세션 1회 진입,
  상태 save/restore 보존). `regression_100`/`smoke_20`/`phase4_compat`/`test_caches`: 실
  PersistentClient → 실 e5 ephemeral. `test_phase1_regression`: subprocess 직전 부모 e5 해제 →
  직후 메인 스레드 재로드(워커 재materialize 회피) + 자식에 `HF_DEACTIVATE_ASYNC_LOAD` 전달.

**개편된 것**
- e5 materialize 정책을 **메인 스레드 1회**로 통일(워커 to_thread 는 encode=싱글톤 재사용만).
  무거운 import(chromadb/embedder/TestClient)를 fixture 로 지연해 collection 시점 DLL 로드
  순서 교란 제거. 테스트 e5/PersistentClient 다중 로드를 세션 단일 자원 대여로 수렴.

**정직성·스코프:** routing(LC)/RPE/BG/CR/swarm 동작 불변(라우팅 결과 불변). 캐시 키 스킴(A1)·
슬롯 디스패치(A3)·의존성 핀(A4) 무변경. 기본 mock·live NO-GO 가드 보존. 키/secret 노출 0.
close() 는 "없는 버그 고친 척" 금지 — 위생+DI 시드로 명시. `HF_DEACTIVATE_ASYNC_LOAD` 는
테스트 하버스 안정화 env(앱 코드 무변경, 회피 아닌 메커니즘 제거).

**테스트(1프로세스):** `pytest tests/` → **1828 passed · 0 failed · 0 errors · crash 0 ·
collection abort 0**(318s, 단일 프로세스). 분할 실행 의존 제거.

**known(비-A5):** `test_step4/step5_1_invariants` 에 선행 미커밋 `read_text(encoding="utf-8")`
1줄이 함께 커밋됨(비대화형 git 분리 불가). 테스트 산출물(`regression_report.md`,
`docs/measurements/*`)·encoding-only isolation 5파일은 미스테이징.

---

### B5 — RPE observe-only 스위치 분리 (observe/active 독립 게이트)

- **상태:** ✅ 완료 · **커밋:** `4773acb` · **일자:** 2026-06-17
- **커밋 제목:** `feat(rpe): split enabled into observe_enabled/active_enabled (observe independently gated, active default off)`

**문제 (v0.7/Phase 6):** `ActiveMutationConfig.enabled` 단일 스위치가 observe 경로
(파이프라인 백그라운드 관찰 태스크)와 active 경로(실제 mutation)를 **동시에** 게이팅했다.
`enabled=False`면 이미 구현된 observe 본체(DopamineRPE.observe/dry_run)까지 통째로 막혀,
"observe 는 production 에서 작동·측정하고 게이트는 측정 위에서 결정한다"는 **모드 1**(2026-06-17
확정)을 코드로 성립시킬 수 없었다(E 그래프 B5 → B6 → C 의존).

**판정:** observe 본체는 **이미 구현**돼 있고 플래그로만 막혀 있었다 → B5 는 관측 로직
신규 구현이 아니라 **스위치 구조 분리 + 게이팅 2줄 재배선**으로 완결(스코프 크립 0).

**추가된 것**
- `ActiveMutationConfig.observe_enabled` / `active_enabled` 두 독립 필드(기본 둘 다 False).
  legacy `enabled` 는 `InitVar` 입력으로만 받아 **observe_enabled 로만** 매핑(active 로는
  절대 0). `enabled` + 명시 `observe_enabled=True` 동시 지정은 모순 → ValueError.
- `tests/phase6/test_rpe_observe_active_split.py`(신규, 11) — 모드 1 핵심
  (observe_enabled=True+active_enabled=False → observe 작동·mutation 0) · observe 양방향
  토글 · active 만이 mutation 게이트 · 하위호환 매핑·충돌 가드 · mutation 계층은
  observe_enabled 미참조(구조 보장).

**바뀐 것**
- 게이팅 2곳 재배선: `pipeline.py`(백그라운드 observe 태스크) `enabled`→`observe_enabled`;
  `service.py`(apply_proposals mutation) `enabled`→`active_enabled`.
- `main.py` 프로덕션 구성 → `observe_enabled=False, active_enabled=False`(둘 다 off).
- `scripts/measure_phase6_final.py` `cfg.enabled`→`cfg.active_enabled`(테스트 미import
  standalone — py_compile 검증). 기존 RPE 테스트 11개를 의도대로 재매핑(service-unit/
  dopamine.apply→active_enabled, pipeline→observe_enabled, 프로덕션 off 불변식→active_enabled).

**개편된 것** — RPE 게이트 의미론을 "관찰 vs 부작용"으로 **명시 분리**. `enabled` 단일
의미(부작용 on)를 폐기하고, mutation 은 오직 `active_enabled` 한 경로로만 게이트된다.

**정직성·스코프 (capability only, 모드 1 성립 전제):** B5 는 observe 를 독립 가동 "가능"하게만
만들고 **프로덕션 observe 활성화는 B6 로 미룬다**(구조와 활성화를 안 섞는다 — A 트랙 원칙).
`active_enabled` 기본 False 는 절대 안전 불변식. routing(LC)/RPE/BG/CR/swarm 부작용 로직
무구현·동작 불변(라우팅 결과 불변). A1 캐시 키·A3 슬롯·A4 핀·A5 conftest 무변경.
CancelledError re-raise·TaskContext 순수 Pydantic·legacy import 0·RPE 내 LLM 0·SwarmTrace
스키마·기본 mock 보존. `rpe.active_skipped` reason="disabled" 로그 계약 유지. telemetry/
demo/routes/스키마 무수정(D 트랙). 키/secret 노출 0.

**테스트(1프로세스):** `pytest tests/` → **1839 passed · 0 failed · 0 errors · crash 0 ·
collection abort 0**(240s). known unrelated: phase1 e5 워커 스레드 materialize 의 A5 잔존
플레이키(첫 run 1회 0xC0000005, 재실행 클린 — B5 무관, e5/embedder/conftest/phase1 무변경).

---

### B6 — 3-mode ablation 측정 harness (observe/dry_run/active, BG raw observations)

- **상태:** ✅ 완료 · **커밋:** `9149c19` · **일자:** 2026-06-17
- **커밋 제목:** `feat(rpe): add 3-mode ablation measurement harness for gate decision (observe/dry_run/active, BG raw observations)`

**문제 (v0.7/Phase 6):** observe/dry_run/active 3모드 측정 도구가 미구현(STEP 6). C 단계
(게이트 확정)가 RPE/BG 동결·해제를 판단할 데이터 근거가 없었다(E 그래프 강제 순서
B6 → C → B8). 단 모드 1(2026-06-17 확정)상 production observe/active 는 off 유지여야 한다.

**핵심 구분(측정용 active ≠ production active):** B6 은 격리된 측정 환경에서 active 를
한 번 돌려 "active 가 만드는 변화"를 데이터로 잡을 뿐, production 을 active 로 켜지 않는다.
measure 는 일회용 in-memory store 만 변형하고 main.py 구성(observe/active off)은 무접촉.

**추가된 것**
- `scripts/measure_3mode_ablation.py`(신규) — 완전 격리·결정론 harness. 고정 grid
  category(7)×difficulty(3)×feature_level(low/high)=42 시나리오. `run_measurement()`
  순수 함수 + `main()` 이 `docs/measurements/three_mode_ablation.{json,md}` 기록.
  app.main/app.state·실 LLM·네트워크·e5/chromadb import 0(같은 입력→같은 출력).
- `tests/phase6/test_3mode_ablation_harness.py`(신규, 11) — 결정론 · AST 격리 가드
  (production/LLM/e5 import 0) · 3모드 대비 · **raw-only 회귀 가드(출력에 agreement_rate/
  match/level 키 부재)** · C 소비 스키마.

**바뀐 것** — 없음(production·RPE/BG/routing 로직·measure_phase6_final 무변경).

**개편된 것** — 없음(신규 측정 도구만 추가).

**정직성·스코프 (raw 측정만, 해석은 C):** RPE 3모드는 격리 store 에서 제대로 측정
(observe decisions / dry_run proposed_delta / active applied_delta, 카테고리별 집계).
**BG 는 raw 만 기록** — bg_recommended(candidate_type)와 routing_chose(path)를 raw 문자열로
나란히 둘 뿐, candidate_type↔path 매핑이나 agreement rate 를 일절 산출하지 않는다(임의 매핑
위 게이트 결정 방지 — 측정→판단 순서 강제, 압축은 비가역). production 구성·routing 결과·
A1/A3/A4/A5/B5 무변경. CancelledError re-raise·TaskContext 순수 Pydantic·legacy import 0·
RPE/BG 내 LLM 0·SwarmTrace 스키마 보존. 키/secret 0(산출물 확인). 생성 산출물은 재생성
가능 → 커밋 비포함(untracked).

**B7·C 소비 연결:** C 는 json 을 읽어 (a) RPE 3모드 delta 차이 → RPE 게이트 판단,
(b) bg_observations raw → candidate_type↔path 매핑·agreement 를 **C 가 직접 정의**해 BG 판단.
B7(BG 배선)은 이 raw 관찰을 배선 위험도 기준선으로 사용.

**테스트(1프로세스):** `pytest tests/` → **1850 passed · 0 failed · 0 errors · crash 0 ·
collection abort 0**(235s). harness 1회 실행 산출물 정상(42 시나리오, agreement_rate 부재,
키 미노출). known unrelated: phase1 e5 워커 스레드 materialize 의 A5 잔존 플레이키
(첫 run 1회 0xC0000005 — phase1_regression subprocess; 재실행 클린, B6 무관).

---

### A5 후속 — 잔존 e5 플레이키 근절 (mmap → disable_mmap)

- **상태:** ✅ 완료 · **커밋:** `2e36aef` · **일자:** 2026-06-17
- **커밋 제목:** `fix(embedder): load e5 with disable_mmap to eliminate intermittent 0xC0000005 (A5 residual flakiness)`

**문제:** A5/B5/B6 세 세션 연속, 전체 1프로세스 `pytest tests/` 가 phase1 e5 첫 로드에서
간헐적으로 Windows 0xC0000005(access violation) 네이티브 크래시(재실행하면 통과 — 플레이키).
B5·B6 엔트리에 "known unrelated"로 기록돼 있던 그 잔여.

**재진단(A5 가설 반증):** A5 는 "워커 스레드 materialize 가 원인, 메인 스레드는 안전"으로
봤으나, 메인 스레드 prime fixture 에서도 동일 크래시가 재현돼 반증. 진짜 뿌리는 스레드가
아니라 **mmap 페이지 폴트** — transformers `_materialize_copy` 가 기본 mmap 된 safetensors
slice 를 `tensor[...]` 로 읽고, 8GB 호스트 commit 압박에서 그 페이지가 resident 못 되면
native page-fault(0xC0000005). 스레드 무관.

**바뀐 것** — `app/core/embedder._get_model()` 의 SentenceTransformer 로드에
`model_kwargs={"disable_mmap": True}` 무조건 적용(환경/테스트 감지 분기 0). safetensors 를
mmap 대신 RAM full read → mmap 페이지 폴트 제거. **production lifespan warmup 까지 근원
해결**(D2 부채였던 항목을 "해결"로 전환). 최악도 native 크래시가 아니라 catchable
MemoryError 로 degrade. 모델 가중치·임베딩·라우팅·출력 불변.

**개편된 것** — A5 가 테스트 경로에서 시도한 메인 스레드 prime 우회는 무효로 판명돼 제거
(신규였던 `tests/phase1/conftest.py` 삭제). A5 free-before-subprocess + 자식
HF_DEACTIVATE_ASYNC_LOAD 는 메모리 동시 상주 방지 belt-and-suspenders 로 유지(변수 분리).

**정직성·스코프:** production embedder 결함의 근원 수정(증상 완화 아님). routing/RPE/BG/CR/
swarm·캐시·슬롯·핀·conftest 격리구조·B5·B6 무변경. 메모리/성능 트레이드오프 정직 기록:
disable_mmap 은 eager RAM read 라 peak ~1.1GB(mmap lazy)→~2.2GB, 첫 로드 ~85s→~150s.

**테스트(반복 안정성):** `pytest tests/phase1/` ×5 → native 크래시 0/5(근원 제거 확인;
free 없는 인공 stress 1회 catchable MemoryError = 의도된 degrade). 전체 `pytest tests/` ×5
연속 → **5/5 green(1850 passed · crash 0 · MemoryError 0)** — 실사용 안정.

---

### B12 — 난이도 5단계 정합 복원 + 난이도→tier 1:1

- **상태:** ✅ 완료 · **커밋:** `5bac013` · **일자:** 2026-06-19
- **커밋 제목:** `fix(routing): restore 5-tier difficulty scale with difficulty→tier mapping (EASY..DEEP_THINKING)`

**문제(분석으로 확정):** `Difficulty` enum 이 3단계(EASY/MEDIUM/HARD)로 축소돼 있었고,
**난이도→tier 매핑 자체가 부재**했다. tier 는 category 가 선택(config category→tier 맵 +
Epinephrine)하고 difficulty(3단계)는 NE·skip_router 경로만 좌우 — 설계·AEV 데모의
"난이도 N → tier N" 5단계 1:1 좌표계(ModelTier)와 어긋난 채 1850 테스트를 통과(테스트가
3단계 양식으로 짜여서 green 이 옳음을 보장 못 한 영역). B11(RPE 난이도 학습)의 선행.

**바뀐 것:**
- `Difficulty` 5단계 — EASY=1/MEDIUM=2/HARD=3/VERY_HARD=4/DEEP_THINKING=5(HARD 이름
  유지, 값 3 ↔ ModelTier.STANDARD 1:1). `EvaluationResult` le=5. `_compute_difficulty`
  5버킷(단조, EASY=1 보존 → Tier-1.5 밴드 불변).
- 난이도→tier 1:1 — LC 가 `selected_tier = ModelTier(int(difficulty))` 직접 결정. 데모
  "난이도 3 → STANDARD 칸" 재현.
- category→tier **폐기(tier 역할만 제거)** — Epinephrine **기관은 보존**. 계속 돌며
  `epinephrine_active/reason`(고연산 신호)만 채우고 tier 제안은 비구속(`tier_suggestion`
  로그). EpinephrineConfig/main 배선 무변경.
- skip_router 5단계 → 3 물리 경로: {1}=lightweight, {2,3}=standard, {4,5}=full_pipeline.
  (HARD 3 이 full_pipeline→standard 로 라벨 이동; path 는 텔레메트리 전용이라 실행 무변경.)
- NE 임계 `difficulty>=4`. ne_boost·planner deep-analysis·planner confidence 모두
  "고난도=>=4" 단일 정의로 통일. ne_reason `difficulty_3`→`high_difficulty`.

**범위 밖(미변경):** RPE decay(휘발, B10), RPE 난이도 학습 key 분해능(B11), 측정
harness(B6), A3 슬롯 모델 차등. SwarmTrace 스키마(difficulty 범위 외) 불변.

**정직성:** Epinephrine 삭제가 아니라 "tier 권한만 제거, 기관 보존"(사용자 지침). category→
tier 강제 매핑 폐기로 난이도가 tier 단일 결정자임을 테스트로 입증(category ⊥ tier).

**테스트:** 16파일(production 6 + tests 10). 신규/갱신 — 5단계 유효·난이도→tier 1:1(3→
STANDARD 재현)·skip_router 5단계·NE>=4 경계·planner 고난도 정의. Epinephrine 기관 보존이라
`test_epinephrine.py`(organ 단위)는 무변경. 전체 1프로세스 `pytest tests/` → **1863 passed**
(크래시 0). 직전 1회차 phase1 subprocess·latency budget 2건은 격리 시 통과(e5 메모리/타이밍
환경 flake, B12 무관) — 재실행 시 1863 clean green.

---

### B11 — RPE 난이도 학습(35칸) + 생체 라우팅 + 단조 래칫 + decay (S1~S5)

- **상태:** ✅ 완료 · **커밋:** `e3f0ee4`(S1) · `b58daaa`(S2) · `6590188`(S3a) · `8ecb507`(S3b-demote) · `ad78b47`(S3b-promote) · `806b95f`(S4) · `e1e561f`(S5) · **일자:** 2026-06-21
- **선행:** B12(난이도 5단계). 5개 메커니즘을 독립 커밋으로 순차 구현(단계별 승인).

**무엇:** RPE 학습 key를 category → **category×difficulty(7×5=35칸)**로 분해하고, 그 학습
가중치가 production 라우팅(메모리 검색 범위 + skip_router 물리 경로)에 실제로 개입하게 만든다.
상승=학습 / 하강=망각의 비대칭 생체 라우팅.

**S1 — 35칸 분리 store(가산):** 신규 `app/rpe/difficulty_store.py`(`(session,category,difficulty)`
키) + difficulty calculator/mutator. 기존 category-only 경로·production SynapseState(7칸)는
무접촉/동결. emergent clamp [0.1,1.0] 상속(난이도별 캡 0, 1.0 천장 불변).

**S2 — 게이트 + 쓰기 활성:** post-response learner가 35칸에 학습 누적(전용 service,
difficulty_learning_enabled). `SynapseDifficultyGate`가 현재 (cat,diff) 칸을 라우팅 직전
snapshot에 읽기전용 오버레이 → CategorySelector→ContextAgent(B12 이후 가중치의 유일한
production 소비처). 35칸 병합 금지(현재 칸만).

**S3 — 생체 라우팅:** (a) S3a 라벨 override — 학습 가중치가 skip_router 밴드 ±1(store 직접
읽기, 미학습 None=무개입 → 시드 leak 없음). (b) S3b-demote — lightweight면 Context Agent
검색 스킵(ChromaDB 0, context_status="skipped"). (c) S3b-promote — **Epinephrine 부활**:
active 조건을 `route_path=="full_pipeline"`로 재정의, ContextAgent threshold 0.4→0.2 유계
확장(read-only, store write 0). tier(B12 1:1) 불변.

**S4 — 단조 래칫:** 세션 내 강등 금지. floor=(session,category,difficulty), B12-native
baseline 포함(난이도 4·5→full_pipeline floor=고난도 자동 보호). override가 floor 아래면
클램프 업. bounded LRU. override-demote는 의도적 차단(강등은 S5로만).

**S5 — decay(래칫의 짝):** lazy 스텝 감쇠 — 칸이 재방문될 때 누적 idle 감쇠 1회 실현(O(1),
sweep 0). weight<0.4면 floor 한 밴드↓(baseline 미만 금지=고난도 영구 보호). 연속/첫 사용=감쇠
0. 상승=학습/하강=망각 비대칭 완성. ⚠️ B4(scheduler rollback)와 별개.

**불변:** B12(enum/tier/NE)·production SynapseState(7칸)·A1/A4/A5·B5·measure harness 무변경.
emergent 1.0 상한 불변(난이도별 비중 밴드는 보장 구조가 아니라 학습 결과로 수용).

**검증:** 단계마다 신규/갱신 테스트(35칸 store·게이트·override·demote/promote·ratchet·decay)
+ 전체 1프로세스 회귀. 최종 `pytest tests/` → **1923 passed**, 크래시 0. (전체 회귀 꼬리에서
`test_phase1_regression` subprocess가 누적 메모리로 간헐 실패 — phase1 직접 10/10·격리 통과로
입증된 known-unrelated e5/메모리 압박.)

---

### B6 (갱신) — 측정 harness: 7칸 t=0 단발 → 35칸 학습 궤적 (faithful+latent 2-pass)

**커밋:** `a1022a1` (2026-06-21). 기존 B6(`9149c19`)을 B11 이후 구조에 맞게 재작성.

**왜:** 기존 harness는 B11 이후에도 frozen 7칸 category-only를 시나리오당 t=0 1회만 재서
35칸 학습·생체 라우팅·래칫·decay를 전혀 안 봤다(C0가 진단한 "단발" 결함이 B11 메커니즘 위에
잔존). 측정 대상이 "7칸 단발"에서 "35칸 학습 궤적"으로 바뀌었으므로 harness를 그에 맞춤.

**⚠️ 분석 중 발견(C가 알아야 함):** 현 production 난이도 학습 실효 보상 소스
HeuristicOutcomeSource는 active 게이트(|PE|≥0.3)에서 **양(+) PE가 +0.20에 막혀 promote가
구조적으로 inert**. MockRewardSource()는 PE=0 기여 0. N 증대로 안 풀림(스텝당 진폭 게이트,
누적 아님). 이 사실이 측정을 두 질문으로 가름 → 사용자 확정 = **2-pass 둘 다**.

- **faithful(안전 근거):** production 실효 소스 [Mock+Heuristic] 그대로. 결과 = clean 셀
  promote 0(seed 동결, active≡observe≡dry_run), failing 셀만 demote(0.1 floor 도달). "현
  보정에서 B11 active는 promote-inert"를 데이터로 증명.
- **latent(값 근거):** harness 전용 outcome→reward 전달함수(positive가 게이트를 넘게)로
  override/ratchet/decay 실구동 — seed 0.3→상승→승급→ratchet 락→idle→decay 한 밴드 해제
  (demote 복원), 고난도(diff5) baseline full_pipeline 면제(보호). ⚠️ 전달함수≠production
  HeuristicOutcomeSource임을 산출물 `latent_caveat`에 명시.

**조작 경계(양 패스):** 시드 조작 0, 라벨 기반 보상 0(보상은 관측 outcome에서 산출, difficulty
미열람), 아키타입은 카테고리별 배정(난이도 무관 probe). **중립성 단언** — 난이도만 다른 셀
(같은 cat·아키타입)은 궤적 비트 동일(`neutrality_checks` 전부 True) = 난이도가 셀 주소만 고르고
delta엔 안 들어감 → "35칸 분화는 시스템 것". 게이트 0.5/0.3 production 그대로(완화 0).

**3-mode 격리:** observe(학습 off)·dry_run(제안만)은 weight 불변·route_path = B12-native 매
스텝, active만 분기. C0 정신 유지 — bg_recommended vs routing_chose **raw 병기, agreement_rate
0**(매핑은 C 위임).

**불변:** production app/ 무변경(측정 도구 — production 컴포넌트 import 격리 실행). B11/B12/
SynapseState/emergent clamp 무변경. B4(scheduler)와 decay 혼동 없음.

**산출물:** `docs/measurements/three_mode_ablation.{json,md}` 칸별 시계열로 재생성(cells/
mode_isolation/ratchet_decay/neutrality_checks/bg_observations), 그리드 난이도 1~5 전체.

**검증:** harness 테스트 신스키마로 재작성(C0 가드 보존+확장: 결정론·no-app.main/no-LLM-e5
격리·raw-only no-agreement + 신규 중립성·faithful inert·latent promote/release 가드) →
13/13. 전체 1프로세스 `pytest tests/` → **1925 passed**, 크래시 0.

---

### B13 — 보상 소스 강화 (양방향 학습이 production 게이트를 넘게, active는 C로 동결)

**커밋:** `754f729`(신호 복원) · `f384e5c`(보상·confidence + 동결) · `f7ed573`(B6 harness),
2026-06-21.

**왜 (B6이 드러낸 더 깊은 진실):** B6은 "promote inert(양 PE +0.20 < 게이트 0.3)"를
발견했는데, B13 분석이 한 겹 더 팠다 — (1) 보상 소스가 **성공 품질을 볼 채널이 비어** 있어
실패(error/timeout/fallback)만 관측, "완벽한 성공"과 "평범"을 둘 다 0.5 중립으로 봄. (2)
delta = PE × **confidence** × max_delta인데 confidence를 0.5로 올리는 유일 경로(expected_*
라벨)가 production에서 절대 안 채워짐 → confidence 영원히 0.3 < 0.5 → **promote·demote 양방향
모두 차단**. 즉 B11 35칸 학습이 production에서 단 한 번도 작동한 적 없음. B13 = B11을
production에서 처음 작동 가능하게 만드는 작업.

**고친 것 (갈래 A 주축 + B 보정, C·D 폐기):**
- **신호 복원(A):** `RPEPipelineSnapshot`/`RPEContext`에 관측 가능 성공 신호 가산
  (`planner_ok`/`generator_ok`/`context_ok`/`clean_finish`/`context_mean_similarity`),
  `pipeline._build_snapshot`이 SwarmResult에서 추출. **라벨 아님, 관측 사실.**
- **보상 가중(A+B):** clean stage +0.04×3, clean_finish +0.08, 관련 컨텍스트 +0.16(유사도
  스케일) — 독립 좋음 **누적**으로 clean+근거 성공이 PE +0.36 > 0.3, 부분/약신호는 sub-gate
  (무차별 칭찬 회피). 음·기존 라벨 항 불변.
- **confidence 기관 복원:** 객관 입증도 기반(명확한 good=clean 파이프라인/완결/근거, 명확한
  bad=error/timeout/fallback → conf↑; 애매=0.3), **상한 0.6**(프로세스 성공 ≠ 정답 검증;
  >0.6은 CP3/user_feedback 예약).

**⚠️ active 분리 (C 경계):** routes.py가 override/ratchet/decay로 35칸 store를 **무조건**
읽으므로, 학습 가능해진 store가 채워지면 route_path가 바뀜. → main.py `difficulty_learning_
enabled=False`(양 서비스, B5 `active_enabled=False` 동결 정신): learn() no-op → store 빔 →
override None → route_path = B12-native 불변. learn→decay→route는 한 store 공유 통합 루프라
정직한 분리 = 루프 전체 off. **보상 복원은 B6 harness로 입증, live production 아님. C가 이
플래그만 켜서 활성화.**

**불변:** 게이트(min_confidence 0.5 / min_abs_PE 0.3) 유지 — 신호를 게이트에 맞춤, 게이트를
안 내림. 35칸 store·mutator·calculator·service·routing·delta·emergent clamp·B11/B12·
SynapseState·A1/A4/A5/B5 무변경. 라벨/기대낮춤(C)/임계완화(D) 폐기. B4(scheduler)와 별개.

**B6 harness 갱신(f7ed573):** faithful 패스가 강화된 production 보상으로 **promote 발화**
(이전 inert → clean 셀 weight 1.0, route full_pipeline 도달), neutral은 sub-gate 유지,
failing은 demote. latent는 같은 신호를 강하게 가중한 상한 probe. 중립성 단언 양 패스 True 유지.
`test_faithful_promote_is_inert` → `test_faithful_promote_now_fires` + 신규 sub-gate 가드.
산출물 재생성(faithful clean seed→1.0).

**검증:** 커밋별 타깃 — 신호 40 passed, 보상/confidence+동결 77 passed, harness 14 passed;
순수 RPE 배치 293 passed(2 transient 실패는 격리 시 통과 = e5-OOM 이웃 후 공유 로거 오염,
B13 아님). 전체 1프로세스 `pytest tests/`는 **8GB 메모리 박스의 급성 e5/safetensors
MemoryError**로 막힘(격리 단일 테스트도 재현 = e5 모델 로드 OOM, B13 무관 — 문서화된 플레이키).
메모리 여유 시 재실행 필요.

---

### B3 — RPE record + 35칸 글로벌 EMA 프리셋 aiosqlite 영속 (C 하이브리드)

**커밋:** `3a662cd`(B3a record) · `d1c5278`(B3b 프리셋), 2026-06-22.

**왜:** C 전에 모드 2(실시간 학습) 안전장치를 완성한다(사용자 결정). RPE record/35칸 store가
전부 in-memory라 재시작 시 소멸 — 학습이 날아가면 모드 2 무의미. **그릇 = aiosqlite**(구조화
키-값 상태), **ChromaDB 아님**(벡터 검색 전용 — 35칸을 욱여넣으면 무의미 유사도+ContextAgent
오염). 기존 aiosqlite 패턴(ExactCache: 자체 DDL + lazy init, `cortex_memory.db` 공유) 재사용.

- **B3a(record 영속):** 신규 `app/rpe/record_store.py`. 적용된 RPEMutationRecord를
  `rpe_mutation_records`에 **raw 1행/mutation**(rollback_id PK). single-apply는 적용 시점,
  영속은 record 확정 후 side-effect라 selection 불변. wall-clock `persisted_at`(monotonic
  `applied_at`과 분리). 서비스에 optional `record_store` 주입(fail-open), 두 서비스 공유.
- **B3b(35칸 프리셋, C 하이브리드):** 신규 `app/rpe/preset_store.py`. 세션 라이브 학습은
  in-memory 그대로(B11 동역학 무변경) + 글로벌 `(category, difficulty)` 35행 EMA 프리셋을
  aiosqlite 영속. `PresettedDifficultyStore`(InMemory 교체): read = 세션→프리셋→None,
  write = 세션 dict만(decay·mutator 둘 다 — DB I/O 0). **EMA roll-up은 학습 mutation 직후
  에만**(difficulty 서비스 post-apply, **decay는 글로벌 미접촉** — 확정). `α=0.3`(B6 후 튜닝).
  lifespan startup이 프리셋 로드(clamp [0.1,1.0]) → 새 세션이 학습값에서 시작.

**B11 정합:** 프리셋(학습된 시작값, None≠)이 mutator의 previous_value seed가 되어 학습이
그 위에 이어짐. 래칫 baseline·decay·single-apply는 시작 상태만 다름(충돌 0). **동결:**
difficulty_learning=False → apply 0 → record/프리셋 write 0(인프라 inert, C에서 채워짐).

**검증:** B3a 8/8 + B3b 12/12(EMA·라운드트립·clamp·폴백·학습전용 roll-up·동결·fail-open) +
각 100-test 회귀, create_app 배선 확인. 무변경: B11 코어·B12·SynapseState·게이트·single-apply·
ChromaDB·B13 동결.

---

### B4 — RPE 자동 rollback scheduler (AsyncIOScheduler timeout, 수동 보존)

**커밋:** `76ee5ab`, 2026-06-22. 모드 2 안전장치의 **마지막 한 축**(decay=점진 망각[B11 S5] +
rollback=즉시 되돌림[B4]).

**의미:** 적용된 mutation은 **잠정** — timeout 내 **확정(confirm) 없으면 자동 rollback**
(revert-unless-confirmed). confirm 트리거(무엇이 검증인가)는 **C 정책**, B4는 메커니즘만 →
그 전까진 모든 mutation 자동 revert(가장 안전한 기본). 동결 중엔 apply 0이라 무동작.

- 신규 `app/rpe/rollback_scheduler.py` `RollbackScheduler`: **AsyncIOScheduler**(Background아님 —
  FastAPI 루프, coroutine job). `schedule(record, rollback_fn)`(now+timeout date job; rollback_fn=
  서비스 바운드 rollback 주입 → 순환 import 0), `confirm(id)`(대기 job 취소=유지), `_fire`(timeout→
  rollback→`rpe.auto_rollback` 로그). **메모리 jobstore**(재시작 시 pending 소멸 — 영속 record
  [B3a]가 재시작 후 수동 rollback 근거, 의도된 단순성 경계).
- 서비스에 optional `rollback_scheduler` + `confirm_mutation()`(C 정책 호출 표면). **수동
  `rollback()` 불변** — 스케줄러가 자동 호출만. difficulty 서비스에만 주입(mode-2 대상).
  lifespan start/shutdown. `config.rpe_rollback_timeout_s`(300s 시작값).
- **apscheduler 승격(A4 이행):** legacy optional 그룹 → 현행 런타임 의존성(이제 실사용).

**rollback ↔ decay 경계:** rollback은 record의 **previous_value(적용 직전값) 복원**("이 변경
undo")이지 시점 되감기 아님 → decay(세션 weight 점진↓, 글로벌 미접촉)와 독립. wall-clock
(apscheduler) vs step(decay). 다중 mutation 순서·confirm 정책은 C 범위(B4는 single-apply·수동
rollback 의미 그대로). ⚠️ B11 S5 decay와 별개.

**검증:** 9/9(schedule 등록·confirm 취소·_fire previous_value 복원·실타이밍 auto-fire·수동
보존·무스케줄러/동결 no-op·생명주기) + 152-test 회귀, create_app 배선 확인. 무변경: B11 코어·
B12·SynapseState·게이트·single-apply·stores·ChromaDB·B13 동결.

---

### B7 — BasalGanglia advisor production 배선 (applied=False 텔레메트리 전용, 단방향 격리)

**커밋:** `f1a98ce`, 2026-06-22. C 전 미착수 B 묶음(B1·B2·B7~B9)의 **첫 작업** — 측정 종속(B6)이
끝나 advisor를 production에 붙인다.

**의미:** STEP 5.1에서 advisor는 구현됐으나 main/routes import 0건(추천 미소비)이었다. B7은 그
advisor를 production 파이프라인에 **단방향**(main/routes → BG) 연결해 **실제 스냅샷 위에서** 추천이
흐르게 하되 행동은 무변경(applied=False). B6는 합성 feature였고 B7은 그 production-fidelity 판본 —
C가 `bg.evaluated`(bg_recommended vs 실제 route_path)를 읽어 매핑·No-Go 안전성을 결정한다. 실제
Go/No-Go 적용(applied=True)은 **C2**.

- routes `_basal_ganglia_observe` 헬퍼: ratchet 직후·route_path 확정 후 호출(정상+continuation
  **양 경로**), `bg.evaluated` 트레이스만. 추천을 route_path/tier/answer로 **되읽지 않음**.
- main `app.state.basal_ganglia = BasalGangliaAdvisor(...)`(stateless, lifespan 무변경).
- **정직 강등(입력 발명 0):** PFC(LC가 async dispatch — 동기 confidence 없음)·LC ne_level(float
  부재, ne_boost bool만)·RPE recent counts(history surface 없음)는 동기 미가용 → None/0. ne_boost를
  근사 ne_level로 매핑하지 않는다(B6 조작 경계 — 측정자가 입력을 발명하면 안 됨). synapse_snapshot만
  실 신호.
- **fail-open 래퍼**(CancelledError만 re-raise): 빌더의 synapse [0,1] 검증이 범위밖 스냅샷에
  raise할 수 있어 자문 실패가 요청을 못 깨게 빌더+evaluate 전체를 감싼다.

**applied=False 2중 잠금:** ① 모델 타입 하드락(`applied=True`면 ValueError) ② 배선 미소비.
**텔레메트리 전용:** TaskContext/QueryResponse/SwarmTrace 스키마 0, app.state엔 `basal_ganglia`
1개·bg_* 0.

**단방향 격리:** BG-leaf(BG는 app.api/main/routing/rpe import 금지)·inner-layer(swarm/pfc/lc/
rpe.pipeline의 BG import 금지) 가드 불변; main/routes→BG 가드만 반전. ADR-015가 배선을 기록하고
ADR-013 격리표를 부분 대체.

**검증:** 신규 `test_basal_ganglia_wiring.py` 7(헬퍼 직접 — e5 미빌드: bg.evaluated·route_path
불변·synapse-only·무변이·fail-open·advisor None no-op) + 격리/step5_1 가드 반전. 전체 1프로세스
회귀 **1969 passed**(중간 STEP4 불변 주석 트립은 가드 미수정·주석 문구 교체로 해소). 무변경: BG
모듈·B11 코어·B12·게이트·B13 동결·ChromaDB·single-apply.

---

### B2 — Synapse WeightUpdatePolicy 死 stub 폐기 (RPE active mutation이 canonical)

**커밋:** `70313f8`, 2026-06-23. C 전 미착수 B 묶음의 두 번째 — 무결점 조건 1("stub·
NotImplementedError 0건")을 향한 정리.

**의미:** `app/synapse/weights.py` `WeightUpdatePolicy.apply_*_rpe`는 Phase 3.5 시대 stub
(NotImplementedError, **고정 델타** +0.15/+0.10/−0.10)으로 7칸 SynapseState에 RPE를 반영할
예정이었으나 **production 배선 0**(어디서도 인스턴스화·호출 안 함). 그리고 **Phase 6 STEP 3.1
(ADR-010)의 RPE active mutation 서비스가 같은 7칸을 이미 더 풍부하게 갱신**한다
(`RPEMutationService` → `SynapseWeightMutator` → `SynapseStoreAdapter`, PE×conf×max_delta·
single-apply·lock·rollback record·영속, `active_enabled=False` 동결). stub은 그것이 대체한
미배선 placeholder.

- **분석 핵심(중복 가르기):** 7칸 production weight-update의 canonical 구현은 **stub이 아니라
  RPE active mutation 경로**. 35칸(B11)은 "never wraps SynapseState"인 별도 backend라 무관.
  WeightUpdatePolicy를 "구현"하면 7칸을 쓰는 **두 번째·발산 writer** + B13이 버린 고정보상
  부활(역행) → (A) 死 stub 폐기로 결정(부활 (B) 배제).
- `app/synapse/weights.py` 삭제(`_clip` 포함 — 외부 소비자 0, 클램프는 categories 상수/RPE
  `_clamp`로 충족). `tests/phase3_5/test_weight_update_stub.py` 삭제(5테스트 전부 대상).
- RPE 격리 가드 **4개 지점/3개 파일**(`app.synapse.weights`/WeightUpdatePolicy)에서 死 항목
  제거 — 삭제된 모듈을 막는 가드는 死 config. 나머지 격리 항목은 RPE 독립 유지.
- **ADR-016** 신규(ADR-010을 canonical updater로 명시) + INDEX. 리빙 doc 정합
  (IMPLEMENTED_ORGANS_AUDIT·PROJECT_STATUS: "제거됨(B2)")은 작업트리 편집으로 달성(사전 변경
  혼입·untracked 회피 위해 B2 커밋엔 미포함).

**검증:** 전체 1프로세스 회귀 **1962 passed**(1969 − 死코드 7테스트, 정확 일치). production
동작 변경 0(미호출 코드). 무변경: B11 35칸·RPE active mutation 서비스·SynapseState 스키마(7칸)·
B12·게이트·B7 BG·ChromaDB·B3/B4.

---

### B9 — GlymphaticCleaner (persistent 저장소 주기 나이 청소, 순수 삭제 no-LLM)

**커밋:** `74d19f7`, 2026-06-23. 미착수 B 묶음의 네 번째 — maintenance 계층 신규.

**빈 자리(분석 확정):** CORTEX엔 청소 안 되는 persistent 무한 누적 둘 — **ChromaDB
semantic_cache**(answer cache + ContextAgent 검색 corpus, eviction 0)와 **aiosqlite RPE
record**(B3a, 적용 mutation 1행씩 영구 append, prune 0). 기존 망각은 대상이 **다름**:
IFOM=goal(in-memory), decay(B11 S5)=가중치 침식, rollback(B4)=mutation 취소, ratchet/
goal_stack=자기 한정 LRU. 역할 한 줄: **"IFOM=goal 망각, decay=가중치 침식, Glymphatic=
persistent 저장소(벡터 캐시 + record) 노후 정리."** (B2 교훈 — 중복 0.)

- **신규 leaf** `app/maintenance/glymphatic.py`: `CleanupStrategy`(ABC)+`DeleteStrategy`(순수
  삭제), `GlymphaticCleaner`가 주입된 `AgeCleanableStore` 프로토콜만 보고 청소 → app.rpe/
  routing/ingress import 0(AST 격리). `STRATEGIES={"delete"}`.
- **나이 기준:** semantic_cache는 `put`에 숫자 `created_at` 스탬프 + `$lt` where 삭제(구
  엔트리 graceful 보존); RPE record는 기존 `persisted_at` prune. **"만료(나이)"만 — "저신뢰"는
  부채**(캐시 메타에 confidence 필드 없음, 흐를 때 확장).
- **압축 전략 자리만:** CompressArchiveStrategy(LLM 요약→아카이브 = 기억 통합, OPERA v1.1
  방향)는 미등록·미구현(NotImplementedError 0). **maintenance no-LLM 불변.**
- **scheduler:** B4 AsyncIOScheduler **공유**(RollbackScheduler 주입구 재사용 + glymphatic
  interval job, `max_instances=1`). 새 인프라 0.
- **안전(게이트 무관 위생 안전판):** `glymphatic_enabled=False` 기본(파괴적 opt-in) · batch
  상한 · 보수 최소나이 · fail-open(한 대상 오류가 사이클 중단 안 함, CancelledError re-raise).
  ChromaDB 삭제는 PLC `protect_chromadb_write` 재사용 — **단 per-trace라 요청 연산과 상호배제
  안 됨**(사이클 자기 직렬화만; 실 가드 = ChromaDB 원자성 + `max_instances=1`). 글로벌 락은
  새 메커니즘이라 **부채**.

**부채(C 선행 아님):** ① "저신뢰" 미구현(confidence 메타 부재) ② PLC per-trace 한계(글로벌
ChromaDB 락) ③ 두 대상 현재 저volume(캐시 write-back 미구현·B13 동결) → forward-looking
위생 인프라(B3/B4 inert-until-C 동형).

**검증:** 신규 22테스트(cleaner 9·semantic age 6·record prune 4·격리 3) + 전체 1프로세스
**1984 passed**(1962 + 22, 정확 일치). 중간 거대 max_age `fromtimestamp` OSError 엣지 →
epoch clamp로 해소. 무변경: B11·RPE 학습·B12·게이트·B7 BG·B3/B4·IFOM/decay/rollback·ChromaDB
검색 읽기. no-LLM·키 0.

---

### B1 — Tier-1.5 diff-edit 실행 (stub → 주입 client, live-only, 코드 내 mock 분기 0)

**커밋:** `d4cc4a9`, 2026-06-23. 미착수 B의 다섯째 — **처음으로 실제 LLM(Flash) 실행을 채우는
실행 기능**(RPE/maintenance의 no-LLM 영역과 다름).

**무엇:** `Tier15Augmentation.execute()`는 모드 무관 `[TIER-1.5 STUB]` 문자열이었다(live에서도
stub). B1이 구현: cache-augmentation 경로가 **LIGHTWEIGHT tier(저비용 슬롯 = "Flash")로 근접
캐시 답(sim 0.75~0.90, EASY)을 새 prompt에 맞게 diff-edit**한다(처음부터 생성 대신 — 비용 절감).

**⭐ mock 실태/범위 판정(분석 핵심):** production 경로에 **산재 "티어 mock 분기"는 없었다.**
mock/live는 `factory.get_llm_client()`의 **단일 DI seam**(env `CORTEX_LLM_MODE` → MockLLMClient/
LiveLLMClient). Tier-1.5는 mock-분기가 아니라 **client 부재 stub**(미구현). → **"티어 mock
걷어내기"는 존재하지 않는 작업, B1 범위 아님.** B1 = stub를 **주입 client로 구현**(미구현 채우기).
- **live-only = 클래스 코드 내 mock 분기 0**: 공유 `app.state.llm_client` 주입(생성자 필수) →
  mock(테스트/기본)/live(프로덕션)는 입구가 결정. execute는 client 종류를 모름.
- **언어 중립 diff-edit prompt**: 영어 지시문이되 **출력 언어를 새 질문 언어로 고정** → 임의
  언어 캐시/prompt 처리(언어별 분기 0).
- **graceful 폴백**: LLM 실패(예외/finish_reason="error") → **cached_response 반환**(이미 근접
  매치; provider/error/키 문자열 surface 0). CancelledError re-raise. EASY·NE 없음 → 기본
  GenerationParams.
- main: `Tier15Augmentation(llm_client=app.state.llm_client)`(공유 client 뒤로 생성 이동).
  routes 호출부(`execute(prompt, cached)`) 불변.

**의존 충족:** A1(캐시 mode-tagging — 캐시 답이 이미 mode-namespaced) + A3(5칸 Tier Slot
Registry + live NO-GO 가드). **게이트 무관**(실행 기능). **키/secret 코드 0**(슬롯이 동적 조회).

**부채(C 선행 아님):** 기본 mock 모드에선 Tier-1.5도 mock 텍스트(시스템 전체와 동일) — 실 Flash
답은 `CORTEX_LLM_MODE=live` + 슬롯 구성(배포 결정). B1은 seam 참여만(전역 mock→live 기본 전환
아님).

**검증:** 신규 5테스트(text 반환·LIGHTWEIGHT+diff-edit prompt·예외 폴백·error finish 폴백·
CancelledError) + test_phase2_pipeline tier_1_5 단언(`[TIER-1.5 STUB]`→`[MOCK LIGHTWEIGHT]`)·
test_routing 픽스처 갱신. 전체 1프로세스 **1989 passed**(1984 + 5). 무변경: A1/A3·B11·RPE·B12·
게이트·B7 BG·B3/B4·B9·ChromaDB·should_activate 밴드.

---

### B8 — Crossroad Reasoning (갈림길 explore, arbitration 아님)

**커밋:** `afe0992`, 2026-06-23. **미착수 B의 마지막** — 끝나면 C 직행.

**정의(재확정):** CR = **Crossroad Reasoning(갈림길 추론)**, ADR-014의 arbitration(BG/RPE
veto·queue·overlay)과 **다른 기관**. RPE override가 고른 1등 route 밴드가 막상막하(학습 가중치가
밴드 임계 0.4/0.7의 `cr_margin`=0.05 이내)일 때, 안정 모드가 확률(`cr_stable_probability`=0.10)로
**인접(탈락) 밴드를 background explore 실행** → 결과를 35칸 학습에 공급(exploit 고착 방지,
"안 뽑던 길도 학습받게"). 마스터 플랜 "충돌 탐지" 문구에 끌려가지 않음.

- **후보 = route 밴드**(플래너·swarm **단일 플랜 무변경** — 최대 구조 변경 회피). 응답은 1등 밴드
  것, explore는 **학습 전용 side run**(사용자에게 탈락 답 안 줌).
- **신규 leaf** `app/routing/crossroad.py`(`CrossroadReasoner`+`CrossroadConfig`): difficulty_store
  읽기 + explore는 **주입 runner**(`rpe_pipeline.execute`)로 → app.rpe.pipeline/swarm import 0.
  routes 양 경로 `maybe_explore`(메인 execute 직후, **background라 user latency 0**).
- **explore→learn**: 기존 swarm + `RPEDifficultyLearner` 재사용(**신규 학습 로직 0**), **별도
  sub-trace**(`{trace}::cr_explore`)로 본 실행과 `(trace,target_key)` **single-apply 충돌 0**.
  decay/rollback/영속 동일 적용. 셀이 (category,difficulty) 키라 explore 보상이 override가 읽는
  같은 셀 갱신.
- **모드**: 긴급(`epinephrine_active`/`ne_boost`) → CR off. **탐색(PFC 지시)은 구조만** — PFC
  explore 신호 미surface(B7 부채)로 도달 0. 안정이 유일 live 모드.
- **2중 동결(C3에서 켬)**: ① `cr_enabled=False`(explore 실행) ② explore의 learn은
  `difficulty_learning_enabled`(B13) 종속 → C 전엔 실행도 학습도 inert.
- **ADR-017** 신규(INDEX 등록), **ADR-014(arbitration) 분리 유지**(덮어쓰지 않음).

**부채(C 선행 아님):** ① 탐색 모드 unreachable(PFC explore 신호 부재 — B7) ② explore 비용
2회차 swarm(확률 amortize·lightweight 경량) ③ 셀 단위 보상 귀속(coarse).

**검증:** 신규 17테스트(reasoner 14: 근접·edge clamp·모드·확률·미학습·fail-open·CancelledError·
config + 격리 3). 전체 1프로세스 **2006 passed**(1989 + 17, 정확 일치). 무변경: 플래너·swarm
단일 플랜·B11~B13 RPE 로직·B12·게이트(0.5/0.3)·B7 BG·B3/B4·B9·ChromaDB·SwarmTrace/스키마. no-LLM·
키 0.

---

### C1 — RPE difficulty learning 활성화 (게이트 트랙 첫 작업)

**커밋:** `10f2088`, 2026-06-23. **OVERTURE에서 가장 무거운 결정 — 한 번도 live로 안 돈 35칸
학습을 production에서 처음 켬.** 지금까지 모든 작업이 이 결정을 안전하게 내리기 위한 준비였다.
C는 셋을 따로 정한다: **C1=켠다 / C2(BG)=동결 유지 / C3(CR)=별도.** 이 작업은 C1만.

**근거:** B13 promote 부활 + B6 측정(faithful 발화·latent 메커니즘·중립성 단언) + 안전장치
decay(B11 S5)·rollback(B4)·영속(B3) 완비. **로직 변경 0 — 게이트만 연다.**

- **Settings 분리(하드코딩 제거):** 신규 `config.rpe_difficulty_learning_enabled`(기본 True, env
  `RPE_DIFFICULTY_LEARNING_ENABLED=false`로 동결). main.py **두 config**가 이 값을 받음 —
  ① 7칸 서비스 config(pipeline의 difficulty learn task **spawn 게이트**) ② 35칸 서비스 config
  (**learner 게이트**). 둘 다 True여야 발화(한 Settings 값이 둘 제어). 35칸 `active_enabled`는
  이미 True(불변), 7칸 synapse 경로(observe=active=False) 무접촉.
- **켜진 효과:** learner가 (category,difficulty) 셀에 write → override/ratchet/decay가 소비 →
  route_path 실제 이동(보상 누적으로 임계 0.4/0.7 넘으면 점진적).
- **B4 자동 revert 기본 유지:** rollback_scheduler 기주입, confirm 정책 미배선 → 적용 mutation은
  timeout 후 자동 revert. **세션 학습 300s 잠정 / 글로벌 EMA 프리셋은 apply 시점 roll-up이라
  revert 무관 영속**(단기 휘발·장기 반복 패턴 영속 — 인간 단기/장기 기억 유사). `confirm_mutation`
  표면 보존(C 후 confirm 정책 배선 가능), scheduler 분리 안 함(안전장치 보존). 첫 live에 보수적.
- **RPE만 분리 활성:** BG(`applied=False` 타입 하드락)·CR(`cr_enabled=False`) **불변** — 독립
  플래그라 difficulty learning만 켜짐. BG=추천 seam+하드락 레일(C2가 applier+플래그), CR=cr_enabled
  (C3) — 하드코딩 아닌 분리 동결 보존.

**검증:** 신규 7테스트(Settings 기본/env override 유닛 + app_client 배선: 두 config True·7칸 동결·
35칸 active 불변·BG/CR 동결·confirm 표면). 전체 1프로세스 **2013 passed**(2006 + 7) — **동결
해제(기본 ON)에도 기존 0개 깨짐**(production 35칸 동결 단언 부재·단발 쿼리 임계 미달 확인).
**무변경:** BG/CR 동결·게이트 임계(0.5/0.3)·B12·ChromaDB·7칸 synapse·decay/rollback/영속 로직.
§4 비목표 문서 충돌은 **C4**가 수정(이 작업은 식별만).

---

### C3 — Crossroad Reasoning 활성화 (게이트 트랙 둘째, 가벼운 플립)

**커밋:** `876c09b`, 2026-06-23. CR explore를 켠다 — **플래그 플립**(CR 메커니즘[B8] 로직 무변경).

- **cr_enabled 기본값 False→True**(이미 Settings 분리 — main 배선 무변경, env `CR_ENABLED`로 끄고
  켜기). description을 활성 명시로 갱신.
- **2중 동결 중 ②는 C1이 이미 열었다**: explore의 learn 게이트(`rpe_difficulty_learning_enabled`)는
  C1으로 True → CR의 남은 동결은 ① cr_enabled뿐이었고, 그것을 켠다.
- **C1+C3 처음 같이 가동**: 막상막하 crossroad(가중치가 밴드 임계 cr_margin 이내)에서 **안정 모드
  10%**가 인접 밴드를 background explore(`rpe_pipeline.execute`, sub-trace `{trace}::cr_explore`) →
  C1이 연 learn 게이트로 **35칸에 실제 공급**. **single-apply 충돌 0**(본 trace vs explore sub-trace
  → `(trace_id,target_key)` 키 분리, 같은 셀에 둘 다 적용). explore mutation도 같은 difficulty
  서비스 경유 → B4 자동 revert·decay·EMA 글로벌 영속 **동일**.
- **안정 모드만 live**: 긴급(epinephrine/ne_boost) off, **탐색(PFC) 도달 0**(신호 부재 부채, 구조
  보존). 발동 희소(crossroad × 10% × 선행학습 필요). 응답은 1등 밴드 것 → **사용자 응답 불변**,
  explore는 background(**latency 0**).
- **BG 동결 유지**(`applied=False` 하드락) — C2는 별도.

**검증:** 신규 5테스트(Settings 기본 True/env override 유닛 + app_client 배선: crossroad enabled·
difficulty_learning still True·BG 동결) + C1 테스트 CR 단언 갱신(`test_bg_stays_frozen`). 전체
1프로세스 **2018 passed**(2013 + 5) — **CR 활성(기본 ON)에도 0 깨짐**. 무변경: BG 동결·게이트
임계(0.5/0.3)·B12·ChromaDB·C1 RPE 로직·CR 메커니즘. §4는 C4.

---

### B10 — 신호 배관: PFC/LC/RPE를 BG에 surface + CR 탐색 모드 (BG 완전 구현 선행)

**커밋:** `a9c0e23`, 2026-06-27. ⚠️ **라벨 주의**: 마스터 플랜 B10(RPE decay)은 B11 S5에 흡수됨;
이 B10은 그 라벨을 재사용한 **신호 배관(BG 완전 입력화)** — 별개 작업. BG를 반쪽 입력으로 켜거나
동결하는 대신 **완전 구현**(사용자 결정)하는 선행: **B10(배관) → 측정 → C2(BG applied) → C4**.

**문제:** BG 점수식 `synapse·0.4 + pfc·0.3 + rpe·0.05 + lc·0.1` 중 synapse만 실값, 나머지 60%는
routes 동기 미가용 → None/0(B7). **⚠️ 셋의 실태가 달라 "기존 신호 배관"이 아니다**(정직성):
PFC=실 신호 有(동기 실행 필요), LC=실 float 無(순수 bool), RPE=집계 자체 無.

- **PFC:** routes에서 PFC 동기 실행(LLM-free) — **goal context**(session_goal_store)로 호출해 swarm의
  goal=None 저신뢰보다 충실. 실 confidence/cue를 BG에 주입. **swarm restructure 안 함**(이중실행 감수).
- **LC:** `ne_boost`(bool)→**{0.0,1.0}**(BG-build 지점). **연속 ne_level 발명 안 함**(NE는 float 없음 —
  LC 연속 재설계는 공개 후 별도 트랙). `modify_params`(bool 소비처)·`neuromodulators.py` 무변경.
- **RPE:** 신규 `app/rpe/recent_counter.py`(`RPERecentCounter`) — C1 실 mutation `applied_delta` 부호를
  (session,category)별 최근 N=20 집계. **read-side**(mutation/single-apply/게이트 무접촉), pipeline
  background post-learn 갱신, routes가 BG용 read.
- **CR 탐색 모드:** PFC에 explore 출력 없음 → **신호 정의**(fallback cue + confidence<0.5 = 확신 없음
  =탐색). `crossroad.maybe_explore`에 `pfc_explore` 인자 + explore-mode 분기(`explore_probability` 50%,
  기존 미사용) 배선. routes-PFC가 그 신호를 CR에 전달.
- **측정:** `scripts/measure_bg_full_input.py` → synapse-only(전부 swarm_full) vs full-input
  (**40% swarm_minimal로 이동**) — 실 신호가 점수 가중치 통해 작용(발명 아님) 입증. `docs/measurements/
  bg_full_input.{json,md}`. **C2(BG applied)의 선행 측정 근거.**

**BG applied=False 유지:** 입력만 채우고 추천은 여전히 미소비(타입 하드락). 행동은 C2까지 동결.

**검증:** 신규 18테스트(recent_counter 7·signal_plumbing 8·crossroad explore 3) + 회귀 12 수정
(헬퍼 시그니처 pfc_decision·RPE 격리 토큰/import 스캔). 전체 1프로세스 **2036 passed**(2018 + 18).
무변경: C1 RPE 로직·게이트 임계(0.5/0.3)·B12·ChromaDB·C3 CR 코어·LC `modify_params`·BG 점수식/하드락.
신호 발명 0.

---

### BG 의사결정 재설계 — 신호 상쇄 탈출 + 라우팅 정합 매핑 (C2 켜기 선행)

**커밋:** `48cb162`, 2026-06-27. C2 분석(BG naive 켜기 = 품질 회귀)에서 사용자 **A 노선**(선결을
메우고 제대로 켜기) 결정에 따른 재설계. **BG applied 켜기 자체는 아님**(C2) — 그 선행.

**문제 (C2 분석이 드러냄):** B10이 입력(PFC/LC/RPE)을 채웠으나 BG type 선택이 **사실상 LC bool 단일
결정함수**였다. `_build_default_candidates`가 4후보에 synapse/pfc/rpe를 **동일 복사** → 점수식에서
공통항(common-mode)이 되어 argmax 기여 0. 유일 차별자 = `lc_caution_bonus`(+0.1, 방어형, NE≥0.5) +
tie-breaker. 게다가 **방향이 라우팅과 반대**: 난이도 4·5(full_pipeline 구간)에서 swarm_minimal(최경량)
추천 → naive applied 시 하드 테일 강등.

- **점수식 (`policies.py`):** 가산합 → **compute-demand 매칭** 재작성. 각 candidate_type은 compute
  레벨 L(full 1.0 > minimal 0.667 > tier1.5 0.333 > fallback 0.0). 컨텍스트는 단일 수요 D = **난이도
  B12 밴드 앵커**(1→1/3, 2·3→2/3, 4·5→1.0 — BG가 라우팅과 일치) **+ 실 신호 부호화 변조**(중립 중심):
  `+ne·0.20`, `+rpe·0.15(2·neg_frac−1)`, `−synapse·0.15(2s−1)`, `−pfc·0.10(2p−1)`. `score=1−|L−D|`.
  결측/중립 신호 → 편차 0(**발명 0**). `_LC_CAUTION_*`·`_rpe_balance` 제거.
- **config (`models.py`):** `ActionSelectionPolicyConfig` → 변조 계수 4개(ne/rpe/synapse/pfc), difficulty는
  구조적 앵커. 4계수 전부 0 → 순수 난이도-밴드 라우팅으로 축퇴.
- **매핑 (`advisor.py`):** `route_path_for_candidate_type()` — candidate_type→밴드(full→full_pipeline,
  minimal→standard, tier1.5/fallback→lightweight). **정의만·미소비**(C2 BG-apply 단계가 소비; production
  라우팅 미호출). 역방향 강등 차단은 **기존 ratchet floor(B11 S4)**가 백스톱. 후보 신호 필드는 이제
  snapshot 전용(점수는 컨텍스트에서 D 산출).
- **측정 (`scripts/measure_bg_redesign.py` → `docs/measurements/bg_redesign.{json,md}`):** 순수·결정론,
  swarm/LLM/e5 없음. **재설계 전**(LC bool): 난이도 4·5 → **378/378 swarm_minimal 강등**. **후**: 난이도
  4·5 → **0/378 raw 강등**(전부 full_pipeline), 난이도 1·3 **3 distinct type**(신호가 선택을 가름), baseline
  초과 승급 84. **C2 정당화 산출물.**

**BG applied=False 유지:** 매핑은 정의만, 라우팅 미소비(C2가 BG-apply 단계 + `bg_apply_enabled` flag로
켬). 모델 하드락 불변.

**검증:** BG 단위 **125 passed**(정책 재작성 21 실패 전부 갱신: 공식·baseline·변조 방향·tie-breaker),
인접 59(3mode 하네스·B10 배관·crossroad·recent_counter·격리 — type 멤버십만 검사 무영향). 전체 1프로세스
**2041 passed**. 무변경: BG 모델 하드락·C1 RPE·C3 CR·게이트 임계(0.5/0.3)·B12·ChromaDB·select() tie-breaker/
confidence 공식. 신호 발명 0.

---

### C2 — BasalGanglia applied 활성화 (승급-전용, OVERTURE 게이트의 마지막 활성화)

**커밋:** `58f8fc0`, 2026-06-27. 재설계로 BG가 올바르게 판단(고난도 강등 0·신호 차별)하게 된 위에서,
그 판단을 **안전한 순서로 라우팅에 적용**. 사용자 결정: BG를 켠다. C2 분석이 "ratchet 이후엔 승급-전용/
floor-존중이 수렴"을 확인 → **승급-전용**이 답.

**적용 위치·방식 (사실):** routes 8.2 — `skip_router → override(S3a) → ratchet(S4, baseline floor
stamp) → route_path 확정 → epinephrine → **BG-apply(8.2)** → swarm → CR`. ratchet이 **이미** floor를
찍은 뒤라(첫 요청에 B12 baseline = 난이도 4·5 full_pipeline) BG가 무엇을 추천하든 baseline 우회 불가.

- **승급-전용 (`policies` 무관, routes apply):** `_basal_ganglia_observe` → `_basal_ganglia_apply`. BG 추천
  `selected_type` → `route_path_for_candidate_type()` → BG 밴드. **밴드 인덱스 클램프**: `band_index(bg) >
  band_index(decision.path)`일 때만 승급, 아니면 무시(강등 추천 버림). 최악 = compute 낭비, **품질 강등
  구조적 0**. 강등 적용은 데이터 축적 후 후속(보수적 1단계).
- **decision 동기 (CR desync 0):** 승급 시 `decision = decision.model_copy(update={path, reason})` +
  `task_context.route_path` 재stamp → 호출부가 갱신된 `decision`을 CR `maybe_explore`로 전달. swarm·CR이
  **동일 경로** 인식. epinephrine 재유도(`path=="full_pipeline"` → limit-break).
- **ephemeral (학습 floor 미오염):** BG는 ratchet 뒤라 세션 floor 미상승 → 승급은 **이번 요청 한정**. 학습
  floor 상승은 RPE 결과 학습으로만. BG는 35칸 store 미접촉(층 분리).
- **플래그 `bg_apply_enabled` (config.py):** 기본 **True**(C1/C3처럼 실제 켬), env `BG_APPLY_ENABLED=false`로
  observe-only 복귀. routes가 `state.settings`에서 읽음(기존 미참조 → 읽기 추가). state.settings 부재 시
  폴백 **False**(테스트/degraded 안전-off) — 프로덕션 기본 True가 켬.
- **하드락 보존:** `ActionSelectionDecision.applied`는 영구 False(타입 레일) — apply는 RouteDecision 조정,
  그 플래그 무접촉. fail-open(CancelledError 재발생, 실패 시 현 decision 반환).

**검증:** test_basal_ganglia_wiring — observe-only 단언 유지(settings 부재 시 폴백 False) + apply-모드 5
신규(승급→route_path·decision↑, full 승급→epinephrine 재유도, 가벼운 밴드→강등 안 함, 플래그 off→observe-only,
ActionSelectionDecision.applied False 유지). test_b10_signal_plumbing은 rename 반영(observe-only). 전체
1프로세스 **2046 passed**(app-build 경로 테스트가 BG apply ON에서 전부 green — 승급이 기존 단언과 정합).
무변경: BG 점수식(재설계)·**모델 하드락**·C1 RPE·C3 CR 코어·게이트 임계(0.5/0.3)·B12·ChromaDB·ratchet/
override/decay·skip_router. §4·§11 문서는 C4. 신호 발명 0.

**남은 게이트:** **C4(§4·§11 비목표 문서)**만 — RPE/BG/CR 활성화(C1·C3·C2)가 끝났으니 설계 문서의
비목표/게이트 상태 정합만 남음.

---

### C4 — CORTEX 5.0 OVERTURE 정본 문서 + 레거시 격리 (OVERTURE 전 트랙 종료)

**커밋:** `17c1d54`, 2026-06-27. **코드 로직 변경 0 — 문서 신규 + docs 이동만.** 게이트 활성화
(C1·C3·C2) 완료 후, 설계 문서를 현행과 정합. 접근(사용자 확정): doc 부채를 하나씩 패치하지 않고
AEV 기준 정본을 **통째 격리**(내용 무수정 = 시점 보존) + 현행 정본 신규 집필. **1차 소스는 코드** —
정본의 모든 수치/목록/버전은 C4 1단계 실측값(추측 0).

**신규 정본 (docs/):**
- `CORTEX_5_0_OVERTURE_ARCHITECTURE.md` — §1 기관 전수(OVERTURE 신규 13모듈 + AEV 기존
  배선/활성화/재설계[BasalGanglia·Synapse·RPE·Tier-1.5·LC/PFC/skip_router·slot_registry] + 순수 AEV
  골격) + 게이트 상태(RPE/CR/BG ON·7칸 동결·Glymphatic opt-in)와 부채(LC 연속값·강등 미적용·CR PFC
  explore) 정직 명시 · §2 의존성+용량(site-packages ~1.33GB[torch 496MB]·e5 ~1.06GB·dim 768) · §3
  구동(uvicorn·CORTEX_LLM_MODE 기본 mock·게이트 env·**키 값 0**) · §4 처리 흐름 **Mermaid flowchart**
  + 단계 설명 · §5 미래 비전(사용자 제공 텍스트 그대로 — CORTEX Suite MSA: Lens·Mirror·Atlas·
  NeuroScope·Relay·Sentinel·Go; **OPERA 제외**).
- `CORTEX_5_0_OVERTURE_METRICS.md` — §6 코드 변화량(**app/ +3,882/−231**, 기준 2841200; 전체 diff
  +54k는 측정 JSON 인플레 주석) · OVERTURE 35커밋/누적 121 · 트랙별 SHA(A/B/C) · 테스트 **2,046
  collected**(디렉토리별) · 종류(단위/통합/격리 AST/회귀/측정 harness/배선/수명주기)·방식(1프로세스·
  lock .venv·e5 플레이키 회피)·정직성(harness 발명 0·결정론).

**레거시 격리 (`docs/legacy/`, 무수정 48파일 이동):** 루트 Phase 산출물(PHASE2~6·PR_DESCRIPTION
3~6)·구-설계도/구조(설계도 v0_6/v0_7·디렉토리 구조 v0_6/v0_7)·감사/분석(2_0_AUDIT·3_1_PERFORMANCE·
GPT의 분석)·IMPLEMENTED_ORGANS_AUDIT(stale, 미커밋 수정 포함 격리)·PROJECT_STATUS·보고서·handoff
PHASE5~6(11)·구판 measurements(phase4/5/6·three_mode .md+.json). **보존(이동 금지):** ADR-001~017+
INDEX(결정 이력)·OVERTURE_VERSION_HISTORY·MASTER_PLAN·README·현행 measurements(bg_redesign·
bg_full_input).

**정합/검증:**
- ⚠️ `similarity_distribution_step3_1.{md,json}`은 `test_similarity_distribution`이 존재를 단언하는
  **live 아티팩트**(epinephrine 임계 0.3948 근거)임이 실측으로 드러나 **격리 제외**, 원위치 유지.
- 깨진 링크: KEPT 문서 중 **version history L5(설계도 링크)만** docs/legacy/ 경로로 갱신. README/
  MASTER_PLAN의 후보 언급은 마크다운 링크 아닌 평문 → 무변경(README 별도 요청 예정).
- **회귀 영향 0:** 전체 1프로세스 **2046 passed**(이동된 measurement를 읽는 테스트 0 — 스크립트
  write 경로뿐). 키/secret 0.

**OVERTURE 결과:** A(기반/정직성)·B(미구현 기능 실구현)·C(게이트 확정)·C4(문서 정합) 전 트랙 종료.
**v1.0 feature-complete + 문서 정합 완료.** 남은 것은 D 트랙(공개/Closeout).

---

### D-prep — 공개 직전 정합 감사 (누락 기관 정본화 + 부채 일부 코드 보수)

**커밋:** `7523b84`, 2026-06-28. 공개(D) 직전, 정본 §1 vs `app/` 전수 대조로 **누락 LIVE 기관**을
발굴하고(추측 0 — basename 기계 대조 후 오탐 제거), 사용자가 고른 범위로 **부채 일부를 코드 보수**.
무거운 재설계(NE 연속값·다국어·ADR-014)는 §1.5 공개-후 트랙으로 분리.

- **누락 기관 정본화(문서):** `neuromodulators.py`의 **Epinephrine·Norepinephrine·Glycine** 3기관 +
  **Continuation Detector·Cue Classifier·Centroid Store**가 §1 organ 목록에서 빠져 있었다(전부 AEV
  LIVE — 死코드/미배선 0). §1.3에 추가. ⚠️ Epinephrine은 `decide()`(LC tier 상향)와 route_path 트리거
  `epinephrine_active`(limit-break) **두 메커니즘 공존**을 명시(혼동 방지).
- **no-GC 무한 성장 해소(코드):** `SynapseStore`(dict→OrderedDict, MAX_SESSIONS=512)·`InMemory`/
  `PresettedDifficultyStore`(dict→OrderedDict, MAX_CELLS=512×35)에 **bounded LRU** 도입(routing_ratchet
  패턴). Presetted evict 셀은 글로벌 EMA 프리셋으로 graceful fallback(영속값보다 나빠지지 않음). 8GB
  호스트 메모리 경계 확보.
- **CR PFC-directed explore 가동+확장(코드):** 실측 결과 explore는 **B10이 이미 배선·도달 가능**
  (routes가 `_pfc_explore_signal` 전달, maybe_explore 분기, 단위테스트 有)이었고 config/crossroad 주석만
  "never reached"로 stale했다. C4가 신호를 **임의 cue의 저신뢰(`confidence < 0.6`)**로 확장(기존 fallback
  cue+conf<0.5 → 경계 goal 매치도 탐색). 실 PFC confidence — **발명 0**. stale 주석 정정.
- **§1.4 부채 정합:** CR explore "미가동" 부채 제거(활성), no-GC·CR 확장은 "공개 직전 해소"로 기록,
  누락 부채(ADR-014 Conflict Resolution·다국어·PLC per-trace·pfc_stub deprecated) 추가.

**검증:** 신규 5테스트(GC evict 3 — Synapse/difficulty/Presetted-evict→preset, CR 신호 확장 2 — 저신뢰
non-fallback 매치→explore/고신뢰 매치→no). 전체 1프로세스 **2051 passed**. 무변경: 게이트(C1/C2/C3)
로직·BG 점수식·ratchet/override/decay·게이트 임계(0.5/0.3)·B12·ChromaDB·METRICS.md·정본 §2~§5.

---

## 3. 갱신 규약 (이 문서 유지보수)

**언제:** OVERTURE 작업(A/B/C/D 항목) 1개의 구현·검증·커밋이 끝날 때마다.

**무엇을:**
1. §2에 엔트리 1개 append(아래 템플릿).
2. §1 현황 표의 해당 행 상태·커밋·일자 갱신.
3. 헤더 "마지막 갱신" 날짜 갱신.

**엔트리 템플릿:**

```markdown
### <ID> — <제목> (<상태>)

- **상태:** ✅/🟡 · **커밋:** `<short-sha>` · **일자:** YYYY-MM-DD
- **커밋 제목:** `<conventional commit subject>`

**문제 (v0.7):** <AEV 현황과 결함>

**추가된 것** — <신규 모듈/필드/테스트>
**바뀐 것**   — <기존 동작/스키마 변경(비파괴)>
**개편된 것** — <구조/SSOT/명명 재정비>

**정직성·스코프:** <불변식 보존: 기본 mock, 키 비노출, 비파괴 등>
**테스트(phase 분할):** <스위트별 결과 + known unrelated>
```

**정직성 규칙:** 미완 작업은 🟡/⬜로 정직하게 표기한다. **커밋되지 않은 작업을 ✅(완료)로 적지
않는다** — 문서가 실제 상태보다 앞서 보이지 않게 한다(A 트랙 불변식의 문서 적용).
