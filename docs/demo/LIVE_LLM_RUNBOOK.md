# LIVE LLM Runbook — CORTEX-AEV `/query` 실답변(live) 운영 절차

이 문서는 `/query`가 **실제 LLM 답변**을 `answer`로 반환하도록 live 모드를 구성·검증·운영하는 절차다.
기본값은 항상 **mock**이며, live는 명시 설정 없이는 켜지지 않는다.

> 보안 원칙: API 키 **값**은 코드/로그/예외/응답/이 문서/커밋 어디에도 넣지 않는다. 환경변수 **이름**만 다룬다.
> `.env`, `config/tier_slots.json`, `test_result*.txt`는 커밋 금지.

---

## 1. 답변 경로 요약 (코드 동작)

- `/query` routed/swarm 경로 → `answer = swarm_result.generator_result.text` (`answer_source="generator"`).
- generator 실패(`finish_reason=="error"`) → `answer = "[ANSWER UNAVAILABLE] generation unavailable"`,
  `answer_source="unavailable"`. **provider/예외/키 문자열은 answer에 절대 포함되지 않는다.**
- 응답 텔레메트리: `answer_source` · `llm_mode`(mock/live) · `swarm_trace.generator_model_name`.
- mock 모드: MockLLMClient 텍스트가 **코어** answer로 흐른다(메타데이터로 식별). **public 데모 UI는 mock 텍스트 비노출.**
- live 모드: LiveLLMClient → 슬롯 → protocol adapter → 실 API 텍스트.

---

## 2. 슬롯 구성 (Tier Slot Registry)

- live는 **5칸(LIGHTWEIGHT·MEDIUM·STANDARD·HEAVY·DEEP_THINKING) 모두 정의 필수**. partial config는 불허(`IncompleteSlotRegistryError`).
- **5개의 서로 다른 키가 필요한 것은 아니다.** 여러 슬롯이 **같은 `api_key_env`(그리고 같은 base_url/model)를 재사용**해도 된다.
  → 2~3개 키만으로 5칸을 채워 smoke 가능.
- `api_key_env`에는 **환경변수 이름만** 적는다(키 값 금지). 로컬/무인증 API는 `allow_empty_api_key: true`.

### 2-1. 2~3키로 5칸 채우는 shared-key 예 (`config/tier_slots.json`)

```jsonc
{
  "LIGHTWEIGHT":   { "base_url": "https://api.vendorA.example/v1", "api_key_env": "CORTEX_KEY_A", "protocol": "openai_compatible", "model": "small-fast" },
  "MEDIUM":        { "base_url": "https://api.vendorA.example/v1", "api_key_env": "CORTEX_KEY_A", "protocol": "openai_compatible", "model": "small-fast" },
  "STANDARD":      { "base_url": "https://api.vendorB.example",    "api_key_env": "CORTEX_KEY_B", "protocol": "anthropic",          "model": "mid" },
  "HEAVY":         { "base_url": "https://api.vendorB.example",    "api_key_env": "CORTEX_KEY_B", "protocol": "anthropic",          "model": "big" },
  "DEEP_THINKING": { "base_url": "https://api.vendorB.example",    "api_key_env": "CORTEX_KEY_B", "protocol": "anthropic",          "model": "big" }
}
```

`.env`(커밋 금지)에는 `CORTEX_KEY_A=...`, `CORTEX_KEY_B=...` 두 개만 둔다.

---

## 3. Preflight (네트워크 0)

```
python scripts/check_llm_slots.py --path config/tier_slots.json
```

- 전 칸 OK/OK_NO_AUTH → `RESULT: GO`(exit 0). 하나라도 문제면 `RESULT: NO-GO`(exit 1).
- 빈 양식 `config/tier_slots.example.json`은 `base_url`/`model`이 비어 **INCOMPLETE → NO-GO**가 정상이다.
- 키 **값**은 출력되지 않는다(env 이름까지만). MISSING_KEY는 env 이름만 보고한다.

---

## 4. Cache 격리 (필수)

영속 cache(ExactCache=SQLite, SemanticCache=ChromaDB)는 **키가 prompt 해시 단독**이라 `llm_mode`/`model`을 담지 않는다.
사전 seed/이전 실행으로 cache에 **mock-시대 답변**이 있으면 live 모드 요청이 그 답변을 그대로 hit할 수 있다.

live smoke 전 다음 중 하나로 격리한다:
- `chroma_path` 디렉터리와 exact cache SQLite 파일을 비운다(clear), **또는**
- 한 번도 쓰지 않은 **fresh prompt / 새 `session_id`**를 사용한다.

> 후속 이슈(별도 PR): cache key 또는 저장 메타에 `llm_mode`/`model_name`/`answer_source`를 포함(또는 모드 태깅)하여
> live가 mock-시대 답변을 서빙하지 않게 한다. 본 변경은 cache 의미를 바꾸지 않는다.

---

## 5. 무네트워크 자동 검증 (CI 안전)

실 API 호출 없이 답변 경로 전체를 검증한다:

```
python -m pytest tests/phase6/test_routes_answer_surface.py -q     # mock surface, live(fake adapter), unavailable, cache 비오염
python -m pytest tests/core/test_check_llm_slots.py -q             # 5칸/shared-key/missing-key(키 미노출)
python -m pytest tests/demo_backend/test_normalize_answer.py -q    # mock 비노출/live 표시/early-exit 라벨/DEMO_REQUIRE_LIVE
python -m pytest "tests/phase5/test_continuation_safety.py::test_session_id_isolation" -q  # 세션 격리(answer 기준)
```

live 경로 테스트는 `httpx.MockTransport`/fake `ProtocolAdapter`만 사용한다 — 네트워크 0.

---

## 6. 수동 live smoke (사용자 명시 승인 후에만)

1. `config/tier_slots.json` 작성(§2, gitignore 확인) — 5칸, 키/env 재사용 가능.
2. `.env`에 env 키 입력(커밋 금지).
3. **Cache 격리**(§4): clear 또는 fresh prompt/session.
4. `python scripts/check_llm_slots.py` → `RESULT: GO`.
5. `CORTEX_LLM_MODE=live`로 앱 기동 후 1~2개 **fresh** routed 쿼리 POST.
6. 확인: `answer`(실답변) · `answer_source=="generator"` · `llm_mode=="live"` ·
   `swarm_trace.generator_model_name` · `selected_tier` · `response_source` · `swarm_trace.elapsed_ms`(latency).
   **artifact에 키 값이 없어야 한다.**
7. 실패 케이스(키 누락 등) → `answer_source=="unavailable"`, `answer == "[ANSWER UNAVAILABLE] generation unavailable"`,
   키/예외 디테일 미노출.

### public 데모 노출 정책
- mock 모드: 데모 UI에 답변 텍스트 비노출(`source="mock_hidden"`), 라우팅/트레이스만.
- live 모드: live answer 표시(`mode="live"`, `source="live_generator"`).
- `DEMO_REQUIRE_LIVE=true`: 비-live run의 answer를 "Live mode unavailable"로 차단(트레이스는 노출).

---

## 7. TODO (이 repo 밖 — 프론트는 별도 repo, 본 PR에서 수정하지 않음)

- [ ] 프론트 about 슬라이드 **1/13** 문구를 "live 답변 연결됨(설정 시), mock 데모는 답변 비노출/라우팅 시각화" 취지로 갱신.
      (프론트 repo는 본 PR 범위 밖 — 코드 수정하지 않고 TODO로만 기록.)
