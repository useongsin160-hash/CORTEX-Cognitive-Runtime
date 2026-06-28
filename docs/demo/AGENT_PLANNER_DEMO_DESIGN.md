# CORTEX-AEV Agent Planner Demo — 설계 문서

> **버전:** v0.2 (Gemini/GPT 교차검토 반영)
> **작성 기준:** CORTEX-AEV Core v0.7, main 브랜치, Windows native 로컬 구동 확인 완료
> **목적:** 개발자 한정 데모 사이트를 통해 CORTEX-AEV가 "단순 LLM 래퍼"가 아니라 "인지 오케스트레이션 런타임"임을 시각적으로 증명한다.
> **상태:** 미해결 질문에 결정 확정됨(섹션 0-2). D1 구현 시작 가능.

---

## 0. 이 문서를 읽는 사람에게

1. **구현자(Claude Code):** 섹션 4~9 사양을 그대로 구현. 임의 판단 지점은 `[구현 주의]`로 표시.
2. **검토자:** 섹션 0-2 확정 결정과 섹션 10 잔여 질문을 본다.

핵심 원칙: **이 데모는 CORTEX core를 수정하지 않는다.** 데모는 `app/`을 *사용*하는 별도 레이어(`demo_backend/`)일 뿐. `app/` 수정, Live LLM 연결, BasalGanglia 라우팅 연결, Conflict Resolution 구현은 **이번 데모 범위에서 전면 금지**.

---

## 0-2. 확정된 설계 결정 (v0.2)

| 질문 | 결정 | 근거 |
|---|---|---|
| CORTEX 호출: HTTP 프록시 vs 직접 import | **HTTP 프록시 (httpx)** | 직접 import 시 모델 로딩·lifespan·메모리가 demo layer와 섞여 8GB Windows에서 재발 위험. 프로세스 분리가 core 오염도 막음 |
| 별도 포트 vs 앱 마운트 | **별도 포트(8001) 유지. 단 HTML은 demo_backend가 서빙** | core 오염 방지 + 프론트 same-origin 확보 |
| Planner 다단계 카드 | **Derived Planner View로 표시, 실제 agent log가 아님을 UI에 명시** | `/query`는 에이전트별 상세 로그 미반환. 정직성 |
| SSE | **D1~D2 생략, 프론트 자체 애니메이션으로 순차 연출** | `/query`가 단발(~90ms)이라 SSE는 연출일 뿐. 복잡도만 증가 |
| stub answer | **답변 영역 축소, CORTEX Analysis/Routing/Swarm Trace 중심 재배치** | answer가 stub이라 답변 중심이면 빈약 |
| HTML 서빙 | **demo_backend가 StaticFiles로 서빙 (확정)** | same-origin → CORS 부담 최소 |
| `/demo/chat` 실행 모델 | **동기 처리 + 인메모리 run store** | `/query`가 90ms 단발이라 background job 불필요. Live LLM 붙는 D4+에서 비동기 재고려 |
| `/trace/{trace_id}` | **best-effort enrichment. 실패 시 `/query` 기반 폴백** | 있으면 풍부하게, 없어도 UI는 뜸 |
| feedback endpoint | **D4로 미룸** | MVP 비핵심 |
| rate limit / 최소 보안 | **D1부터 최소 구현** | Live LLM 붙일 때 안 흔들리도록 |
| Live LLM 연결 | **이번 데모 미연동. D4 이후 별도 gate** | 프로젝트 정책상 gated next step |

---

## 1. 확정된 현실 (Ground Truth)

### 1-1. CORTEX 백엔드는 Windows에서 작동한다
- `uvicorn app.main:app --host 127.0.0.1 --port 8000` 정상 기동
- 모델(`intfloat/multilingual-e5-base`, 로컬 HF 캐시) 정상 로드
- 회귀 테스트 1716/1717 통과 (1개는 8GB RAM subprocess 이중 모델 로드 환경 제약, 코드 무관)

### 1-2. 백엔드 엔드포인트 (실측)
```
GET  /health
POST /query               → 핵심. 전체 결과를 단일 JSON으로 반환
GET  /trace/{trace_id}
```

### 1-3. `POST /query` 실측

요청:
```json
{ "prompt": "안녕, 테스트 쿼리야", "session_id": "string" }
```

응답(200):
```json
{
  "trace_id": "fc9c24ba183247ae91e823de51f26d82",
  "answer": "Phase 2 stub - routed to lightweight (tier=STANDARD, epinephrine=false)",
  "path_taken": "routed_lightweight",
  "route_decision": {
    "path": "lightweight",
    "skip_layers": ["full_planner", "basal_ganglia_cr"],
    "reason": "difficulty 1 — Tier-1.5 branch may apply downstream"
  },
  "difficulty": 1,
  "category": "coding",
  "selected_tier": "STANDARD",
  "epinephrine_active": false,
  "epinephrine_reason": "similarity_gate_fail",
  "response_source": "swarm",
  "swarm_trace": {
    "executed": true, "status": "ok", "elapsed_ms": 90.06,
    "context_status": "empty", "planner_status": "ok",
    "generator_status": "ok", "generator_finish_reason": "stop",
    "plan_intent": "code_generation"
  },
  "glycine_active": false, "glycine_reason": null, "glycine_action": null
}
```

### 1-4. 현실 제약 두 가지

**(A) `answer`는 stub.** 실제 LLM 결과 아님(`.env` 키 미설정 + 정책상 gated). → 데모 가치는 답변이 아니라 **인지 처리 과정 시각화**에 있고, 그 데이터는 이미 진짜로 나온다.

> **갱신(live LLM answer path 연결됨)**: 위 (A)의 "answer는 stub" 전제는 더 이상 정확하지 않다. routed/swarm 경로는
> 이제 Generator 텍스트를 `answer`로 반환한다(`answer_source`/`llm_mode`/`swarm_trace.generator_model_name` 노출).
> 다만 **기본 mock 모드에서는 public 데모가 mock 답변 텍스트를 노출하지 않으므로**(`source="mock_hidden"`),
> 이 문서의 "답변 영역 축소·인지 과정 중심" 설계 방향은 mock 데모에서 그대로 유효하다.
> live 모드(운영자 설정)에서는 실제 답변이 노출된다. 아래 예시 JSON의 `"Phase 2 stub ..."` answer 값은 옛 동작이다.
> 상세: [docs/demo/LIVE_LLM_RUNBOOK.md](LIVE_LLM_RUNBOOK.md), [docs/IMPLEMENTED_ORGANS_AUDIT.md](../IMPLEMENTED_ORGANS_AUDIT.md) §0.

**(B) `/query`는 단발(~90ms).** → 단계별 표시는 백엔드 스트리밍이 아니라 **프론트 연출**. SSE는 D1~D2 생략.

---

## 2. 데모 제품 정의

**이름:** CORTEX-AEV Planner Console

> CORTEX-AEV is not a chat wrapper. It is a cognitive orchestration runtime that routes, plans, executes, observes, and traces agentic tasks.

**성공 기준:** 프롬프트 입력 → CORTEX가 실제로 분류·난이도·라우팅 → UI가 인지 단계 순차 시각화(실데이터) → swarm_trace 실행정보 표시 → Safety State 노출 → trace_id 재조회.

**비목표:** 실제 LLM 답변 / production active learning / BasalGanglia 라우팅 연결 / Conflict Resolution — 전부 안 함.

---

## 3. 전체 아키텍처

```
[브라우저]
   │  GET /         → demo_backend가 HTML 서빙 (same-origin)
   │  fetch /demo/* → demo_backend API
   ▼
[demo_backend]   (FastAPI, 포트 8001)
   │  내부 httpx (서버→서버)
   ▼
[CORTEX core]    (app.main:app, 포트 8000)  ← 절대 수정 안 함
   ▼
[Embedder / ChromaDB / SQLite]
```

**핵심: 브라우저는 CORTEX(8000)를 직접 호출하지 않는다.** 브라우저는 demo_backend(8001)와만 통신(same-origin), CORTEX 호출은 demo_backend가 서버 사이드 httpx로 대신.

`[CORS 명확화]` HTML을 demo_backend가 서빙 → 브라우저↔demo_backend same-origin → CORS 불필요. demo_backend↔CORTEX는 서버 간 호출이라 브라우저 CORS와 무관. 방어적으로 `CORSMiddleware`에 `127.0.0.1:8001`, `localhost:8001`만 허용.

---

## 4. demo_backend API 사양

베이스: `http://127.0.0.1:8001`

### 4-1. `GET /`
`demo_frontend/index.html` 반환 (StaticFiles).

### 4-2. `POST /demo/chat`
프롬프트를 받아 **동기적으로** CORTEX `/query` 호출, 결과를 인메모리 저장 후 run_id 반환.

요청: `{ "session_id": "demo_s_123", "message": "...", "mode": "agent_planner" }`
응답: `{ "run_id": "run_abc", "status": "done", "result_url": "/demo/runs/run_abc" }`

`[구현 주의]` `/query`가 90ms 단발이라 background queue 불필요. 내부에서 바로 호출→저장→반환, status는 이미 done.
`[구현 주의]` best-effort enrichment: trace_id 획득 후 가능하면 `GET localhost:8000/trace/{id}`도 호출. **실패해도 `/query` 결과만으로 정상 동작.**

### 4-3. `GET /demo/runs/{run_id}`
정규화된 전체 결과를 단발 JSON 반환. 프론트가 받아 자체 애니메이션으로 순차 표시.

### 4-4. `GET /demo/readiness`
표시 상태 + 실행 게이트 분리:
```json
{
  "cortex_reachable": true,
  "cortex_url": "http://127.0.0.1:8000",
  "demo_mode": "stub",
  "llm_key_present": false,
  "llm_live_enabled": false,
  "can_run_query": true,
  "can_run_live_llm": false,
  "active_learning_enabled": false,
  "basal_ganglia_applied": false,
  "conflict_resolution": "deferred",
  "warnings": [
    "Live LLM answer generation is not connected.",
    "Planner cards are derived from route_decision and swarm_trace."
  ]
}
```
`[구현 주의]` `cortex_reachable`은 `/health` 호출 판정. CORTEX 꺼져 있으면 `/demo/chat`은 503으로 차단(실행 게이트). `llm_key_present`는 presence만, **키 값 노출/로깅 금지**.

### 4-5. `GET /demo/health`
demo_backend 자체 헬스체크.

---

## 5. CORTEX 응답 → UI 매핑

| UI 요소 | 출처 (`/query`) | 비고 |
|---|---|---|
| 헤더 Safety State | `/demo/readiness` | 4종 |
| SemanticEvaluator | `category`, `difficulty` | |
| Routing | `route_decision.{path,skip_layers,reason}` | |
| Tier | `selected_tier`, `epinephrine_active` | |
| AsyncSwarm | `swarm_trace.{executed,status,planner_status,generator_status}` | |
| Glycine | `glycine_active/reason/action` | |
| Answer 패널 | `answer` | **stub. "LLM gated" 배지 필수** |
| 우측 Planner Board | `route_decision` + `swarm_trace.*` | **Derived View 배지 필수** |
| Metrics | `swarm_trace.elapsed_ms`, `response_source` | |
| trace_id | `trace_id` | |

### 5-1. Derived Planner View 원칙 (필수)
`/query`는 PlannerAgent/RiskAgent/TestAgent별 상세 로그를 반환하지 않는다. 우측 Planner Board는 `route_decision`, `difficulty`, `category`, `swarm_trace.plan_intent/planner_status/generator_status` 기반의 **파생 시각화**다.
- **"Derived from CORTEX trace"** 배지 필수.
- 문구: "이 영역은 CORTEX의 실제 swarm_trace와 route_decision을 기반으로 구성한 데모용 Planner View입니다. 현재 CORTEX /query 응답은 에이전트별 상세 로그를 직접 반환하지 않습니다."
- **금지:** 설명 없이 "PlannerAgent 완료 / RiskAgent 완료"를 실제 로그처럼 표시.

### 5-2. Stub Answer 표시 정책 (필수)
중앙 패널 제목을 `Final Answer` → **`CORTEX Execution Result`**. 3단 구성:
1. **CORTEX Analysis** — category/difficulty/route path/selected tier
2. **Runtime Trace** — swarm executed/planner status/generator status/elapsed_ms
3. **Answer** — 현재: Stub / Live LLM: gated, not connected
- **"LLM generation gated / stub mode"** 배지.
- 문구: "현재 답변 생성은 Live LLM 미연동 상태라 stub으로 표시됩니다. 이 데모의 핵심은 CORTEX의 라우팅, 난이도 판단, 실행 trace 시각화입니다."

---

## 6. SSE 보류 결정
**D1~D2에서는 SSE를 구현하지 않는다.** `/query`는 단발이고 프론트는 이미 자체 순차 점등 연출을 가짐. MVP는 `/demo/chat` + `/demo/runs/{id}` 단발 JSON으로, 순차 표시는 프론트가 담당. SSE는 Live LLM/장기 실행이 추가되는 **D3+에서 재검토**.

---

## 7. 프론트엔드 연결

### 7-1. 파일 위치
```
Local source:        %USERPROFILE%\Downloads\*CORTEX*.html
Project destination: demo_frontend/index.html
```
`[구현 주의]` 정확 파일명은 `dir /s /b "%USERPROFILE%\Downloads\*CORTEX*.html"`로 확인 후 복사:
```cmd
copy "%USERPROFILE%\Downloads\<실제파일명>.html" demo_frontend\index.html
```

### 7-2. 연결 (상대경로, same-origin)
```js
const r = await fetch('/demo/chat', { method:'POST',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({ session_id, message, mode:'agent_planner' }) });
const { result_url } = await r.json();
const data = await (await fetch(result_url)).json();
// data를 기존 프론트 애니메이션 로직에 주입
```
`[구현 주의]` StaticFiles 서빙이라 브라우저↔demo_backend same-origin → CORS 문제 없음.

---

## 8. 보안·안전 정책

### 8-1. D1부터 필수
- API key 값 로깅 금지. readiness는 **presence만**.
- `.env`, `test_result*.txt`, `multilingual-e5-base.tar.gz`, 모델 아카이브 **커밋 금지** → `.gitignore` 점검.
- demo_backend는 `127.0.0.1` 바인딩만.
- request body size 제한.
- session_id별 rate limit: 분당 10 / 세션당 50 / 앱 누적 500(설정값).
- CORTEX URL은 env, 기본 `http://127.0.0.1:8000`.
- Safety invariant UI 상시 노출: active_learning=false, basal_ganglia_applied=false, conflict_resolution=deferred, mutation_count=0.
- 시스템 프롬프트 원문·raw 요청·키 값은 trace 표시 금지.

### 8-2. D4로 미룸
로그인 / 피드백 저장 / 관리자 페이지 / 비용 대시보드.

---

## 9. 구현 단계

```
D0. (완료) 설계 확정 + 교차검토
D1. demo_backend 스켈레톤
    - FastAPI, 포트 8001, httpx 프록시
    - GET / (StaticFiles), /demo/health, /demo/readiness
    - POST /demo/chat → /query 동기 호출 → run_id → 인메모리 저장
      (best-effort /trace/{id} enrichment, 실패 시 /query 폴백)
    - GET /demo/runs/{run_id}
    - CORSMiddleware(방어적), request size limit, 최소 rate limit
    - SSE 없음
    검증: curl로 /demo/chat → /demo/runs/{id}가 실제 CORTEX 데이터 반환?
D2. 프론트엔드 연결
    - HTML → demo_frontend/index.html, mock → fetch('/demo/...')
    - StaticFiles 서빙, Derived/Stub 배지 적용
    검증: 브라우저에서 프롬프트 → 실제 trace가 UI에?
D3. (선택) SSE — Live LLM/장기 실행 추가 시.
D4. 안전장치 마감 + (선택) feedback / live LLM gate.
```

**D1→D2가 핵심.** 목표는 예쁜 UI가 아니라 `/demo/chat → /query → /demo/runs/{id} → 프론트 표시`가 실데이터로 이어지는 것.

---

## 10. 잔여 질문 (구현 중 결정)
1. Derived Planner View 카드 개수 (plan_intent 1~2장 vs route_decision 합쳐 3~4장) → D2에서 결정.
2. rate limit 저장소 인메모리 dict로 충분 (단일 프로세스) → D1 인메모리.
3. Live LLM 이번 데모 제외 (보류 아닌 명시적 제외, D4 이후 gate).

---

## 11. Demo Prompt Set

**A — 제품/개발 계획**
```
내가 CORTEX-AEV 데모 사이트를 개발자 한정으로 배포하려고 해.
백엔드, 프론트엔드, API 키 보안, 비용 제한, 테스트 계획까지
2주짜리 실행 계획으로 쪼개줘.
```
**B — 장애 분석**
```
다음 상황을 분석해줘.
Windows 8GB 환경에서 모델 로딩 중 os error 1455가 발생했고,
프로그램을 닫자 uvicorn은 성공했지만 full pytest의 subprocess 메타 테스트는 실패했다.
원인, 우회책, 데모 진행 판단, 검증 계획을 나눠줘.
```
**C — 보안/운영 설계**
```
개발자 한정 AI 데모를 안전하게 공개하려고 해.
API key 노출 방지, rate limit, 비용 상한, 로그 redaction,
trace_id 기반 피드백 수집, abort criteria를 포함해 운영 체크리스트를 만들어줘.
```

---

## 부록 A. 폴더 구조
```
CORTEX---AEV/
  app/                      ← CORTEX core. 절대 수정 안 함.
  demo_backend/
    __init__.py
    main.py                 ← FastAPI (8001), StaticFiles 서빙
    cortex_client.py        ← httpx로 localhost:8000 호출 (+ trace enrichment)
    models.py
    settings.py             ← env (CORTEX_URL 등)
    rate_limit.py           ← 인메모리 세션 rate limit
  demo_frontend/
    index.html              ← Claude Design HTML
  docs/demo/
    AGENT_PLANNER_DEMO_DESIGN.md
```

## 부록 B. 실행
```bash
# 터미널 1
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# 터미널 2
.venv\Scripts\python.exe -m uvicorn demo_backend.main:app --port 8001
# 브라우저
http://127.0.0.1:8001/
```
`[구현 주의]` 8GB RAM에서 모델 로드는 CORTEX(8000) 하나뿐. demo_backend(8001)는 HTTP 프록시라 모델 직접 로드 안 함 → 추가 메모리 부담 작음.
