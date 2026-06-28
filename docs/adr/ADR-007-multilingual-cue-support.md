# ADR-007: Multilingual Cue Support

## Status

**Proposed** — Phase 6 or Later (2026-05-24, Phase 5 STEP 7)

---

## Context

Phase 5 STEP 5에서 `app/routing/cue_classifier.py`를 도입하여 continuation cue를
탐지한다. 현재 지원 언어는 한국어와 영어 두 가지로 제한된다.

CORTEX-AEV는 임베딩 단계에서 `intfloat/multilingual-e5-base`(100+ 언어)를
사용하지만, cue keyword detection은 다음 두 언어 중심이다.

**현재 지원**:
- Korean (한국어)
- English

**현재 미지원**:
- Japanese (日本語)
- Chinese (中文)
- Spanish (Español)
- French (Français)
- German (Deutsch)
- 기타 multilingual-e5-base가 지원하는 언어

Phase 5 STEP 6 측정에서는 한/영 cue 기준 false positive 0건, bypass accuracy 100%로
잘 동작하지만, 다국어 사용자 시나리오는 측정 대상에 포함되지 않았다.

---

## Decision

Phase 5에서는 **한/영 cue만 지원**한다.

한/영 외 cue 확장은 Phase 6 또는 별도 Phase에서 검토한다. 본 ADR은 그 결정의
근거 자료로 작성된다.

### 검토 후보

#### Option A: Language-specific keyword expansion

언어별로 `CueClassifier`에 keyword/regex를 추가한다.

```python
# 예시
_JA_CONTINUATION_KEYWORDS = frozenset({"続けて", "次", "進めて", "もっと"})
_ZH_CONTINUATION_KEYWORDS = frozenset({"继续", "下一个", "继续做"})
_ES_CONTINUATION_RE = re.compile(r"\b(?:continúa|continuar|siguiente|sigue)\b")
```

- **장점**: 명확하고 빠름. word-boundary / regex 기반이므로 latency 영향 최소.
- **단점**: 언어별 false-positive guard 유지 비용 증가. 언어 식별이 mixed 케이스에서 어려움.

#### Option B: Embedding-based cue classification

cue prototype embedding과 query embedding의 cosine similarity로 분류.

- multilingual-e5-base로 cue prototype 사전 임베딩 (한 번)
- 매 query마다 cosine similarity 계산
- threshold 초과 시 해당 cue 카테고리로 분류

- **장점**: 언어 확장성. 한 번 prototype을 만들면 100+ 언어 커버.
- **단점**: false positive 위험 (의미적으로 유사한 비-cue 표현). embedding 비용으로
  latency 증가. continuation bypass는 30ms 이내 결정이 필요하므로 embedding cost가 부담.

#### Option C: Hybrid

- Korean/English: keyword + regex (현재 방식 유지)
- Other languages: curated keyword OR lightweight embedding gate

- **장점**: 정확도와 확장성의 균형.
- **단점**: 코드 복잡도 증가. 두 경로 모두 유지 필요.

---

## Consequences

### Phase 5에 남기는 부채

- 한/영 외 cue 미지원 (Japanese, Chinese, Spanish, French, German 등)
- 임베딩 기반 cue 분류 미구현
- 실제 사용자 데이터 기반 cue 확장 미수행
- 다국어 false-positive/false-negative 측정 미수행

### 영향 범위

- continuation bypass는 한/영 cue가 없는 query에서 발동하지 않음 → fail-open으로
  normal path 진행 → 정확성 손실은 없으나 latency 단축 효과 누락.
- multilingual-e5-base 임베딩 자체는 Evaluator/Synapse에서 모든 언어 처리 가능.

---

## Resolution Plan

Phase 6 또는 별도 Phase에서:

1. 실제 cue usage 데이터 수집 (로깅 기반)
2. 언어별 cue 빈도 / false positive / false negative 측정
3. Option A / B / C 비교 측정 — bypass accuracy, latency, maintenance cost
4. 본 ADR을 **Accepted / Rejected / Superseded** 중 하나로 갱신
5. 결정 후 cue_classifier 구현 확장 또는 별도 모듈 분리

---

## References

- `app/routing/cue_classifier.py` — 현재 한/영 cue 구현
- `docs/handoff/PHASE5_STEP5_CONTEXT.md` — cue 매트릭스 (한/영)
- `docs/measurements/phase5_step6_pfc_impact.md` — Phase 5 cue 측정
- `PHASE5_COMPLETE.md` — STEP 7 closeout
