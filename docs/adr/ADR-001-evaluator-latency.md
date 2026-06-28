# ADR-001: SemanticEvaluator 응답 시간 목표 재산정

## Status
Accepted

## Context
설계 문서의 SemanticEvaluator 응답 시간 목표 `< 20ms`는
ChromaDB 기본 임베더(all-MiniLM-L6-v2, 22M params, CPU ~5-10ms inference)
사용을 가정한 값이었다.

Phase 3 STEP 1.5에서 한국어 cross-lingual 정확도 확보를 위해
임베더를 `intfloat/multilingual-e5-base` (278M params, CPU 35-80ms inference)로
교체했다. 이 결정으로 기존 응답 시간 목표 가정이 깨졌다.

STEP 2 실측 결과 (30 샘플, 워밍업 이후):
- 평균: 56.34 ms
- p50: 55.34 ms
- p95: 77.94 ms
- p99: 79.19 ms
- 병목: 임베더 inference time (mean-centering + dot-product는 < 1 ms)

## Decision
응답 시간 목표를 다음과 같이 재산정한다:

- **평균 < 80 ms**
- **p99 < 150 ms**

기각된 대안:

- **옵션 B (ONNX 변환)**: Phase 4 이후 최적화 카드로 보류.
- **옵션 C (int8 quantization)**: 정확도 손실 위험. 사용자 정확도 철칙 위반.
- **옵션 D (경량 임베더 회귀)**: STEP 1.5 cross-lingual 7/7 결정 후퇴.
- **옵션 E (Evaluator 결과 캐시)**: ExactCache와 역할 중복.

## Consequences

긍정적:

- cross-lingual 정확도 7/7 유지
- 한국어 사용자 분류 정확도 보장
- 정확도 철칙 ("한 글자의 오차도 안 됨") 부합

부정적:

- 단일 evaluator 응답 시간 약 3-4배 증가 (20 → 80 ms)
- Phase 4 Async Swarm 구현 전까지 wall-clock latency 영향 존재

완화 방안:

- Phase 4 Async Swarm에서 evaluator + PFC dispatch 병렬화로 wall-clock 영향 완화.
- ADR-002에 따른 임베딩 공유 파이프라인 도입으로 추가 단축.

## References
- 설계 문서: Semantic Evaluator 섹션
- STEP 1.5 옵션 F 결정 기록
- STEP 2 실측 데이터
- ADR-002 (후속 최적화)
