# ADR-002: Phase 4 임베딩 공유 파이프라인 도입

## Status
Proposed (Phase 4 진입 시 실행)

## Context
현재 `/query` 파이프라인에서 SemanticCache와 SemanticEvaluator가
동일 임베더를 독립적으로 호출하여 같은 prompt에 대해 임베딩 계산을
2회 수행한다. 각 호출에 ~56 ms 소요.

현재 동작:

```
prompt
  → semantic_cache.get(prompt)         [embed: 56 ms]
  → semantic_evaluator.evaluate(prompt) [embed: 56 ms]
총 임베딩 비용: 112 ms
```

최적화 후 (Phase 4):

```
prompt
  → embed(prompt)                                 [56 ms, 1회]
  → semantic_cache.get_by_embedding(embedding)    [~5 ms]
  → semantic_evaluator.evaluate_by_embedding(embedding) [~5 ms]
총 임베딩 비용: 66 ms
```

## Decision
Phase 4 Async Swarm 구현 시 다음을 적용:

1. `/query` 엔드포인트에서 prompt 임베딩을 1회 계산하여 컨텍스트에 보관
2. `SemanticCache.get_by_embedding()` 인터페이스 추가
3. `SemanticEvaluator.evaluate_by_embedding()` 인터페이스 추가
4. 기존 `get(prompt)` / `evaluate(prompt)` 인터페이스는 fallback으로 유지

## Consequences

긍정적:

- 임베딩 중복 호출 제거로 wall-clock 약 40% 단축
- 시스템 전체 latency 개선

부정적:

- API 인터페이스 추가 (복잡성 증가)
- 임베딩 객체의 lifecycle 관리 책임 추가

## Triggering Condition
Phase 4 진입 시점에서 본 ADR을 참조하여 작업에 포함.

## References
- ADR-001
- 설계 문서: Phase 4 Async Swarm 섹션
