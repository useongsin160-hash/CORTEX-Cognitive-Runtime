# ADR-003: 에피네프린 threshold 0.3948의 임시성

## Status
Accepted (Phase 3)

## Context
STEP 3.1 측정에서 HIGH 카테고리 p50 self-similarity = **0.3948**을
에피네프린 신뢰도 게이트 threshold로 채택했다.

이 값은 다음 조건의 산물이다:

- 시드 데이터: `tests/phase3/seed_queries.json` v2.0 (70개 / 140 표현)
- 임베더: `intfloat/multilingual-e5-base`
- 정규화: global-mean centering (CACHE_VERSION `v2.1_e5_bilingual`)
- 측정 시점: STEP 3.1

또한 STEP 3.1 측정은 다음 사실을 드러냈다:

- LOW 그룹 평균 self_sim (0.4105)이 HIGH 그룹 평균 (0.3712)보다 높다.
- 즉 single-threshold-on-self-sim 게이트로는 HIGH/LOW가 분리되지 않는다.
- 이에 따라 게이트는 **카테고리 게이트 + 신뢰도 게이트** 형태로
  AND 결합 (STEP 3.2 결정 1). threshold 단독은 1차 결정 신호가 아닌
  HIGH 카테고리 내부의 confidence cutoff로만 작용한다.

## Decision
threshold 값을 `app/core/config.py::EpinephrineConfig.similarity_threshold`로 분리한다.

다음 조건 발생 시 재측정 및 갱신:

1. 시드 데이터 추가/변경 (`tests/phase3/seed_queries.json`).
2. 임베더 교체 (`app/core/embedder.py`).
3. centroid 재빌드 — mean-centering 좌표계가 바뀌면 절대값 해석이 달라진다.
4. Phase 6 Dopamine RPE 도입 — 동적 조정이 시작되면 본 ADR은
   `Superseded by` 상태로 전환된다.

## Consequences

긍정적:

- threshold가 코드 본체에서 분리되어 동적 조정 가능.
- Phase 6 RPE 학습 신호를 받기 위한 단일 변경 지점 확보.
- 임시값임이 ADR에서 명시되어 "왜 이 숫자?"에 대한 추적 가능.

부정적:

- mean-centering 컨텍스트 정보 없이 값을 보면 0.39라는 숫자가
  일반 cosine 감각 (0~1)에서 낮아 보일 수 있다. 해석할 때 항상
  ADR-001 + STEP 1.5 closeout 노트를 같이 본다.
- LOW 그룹 self_sim이 HIGH보다 높다는 STEP 3.1 발견에 따라 단독
  threshold 게이트는 부적합. 카테고리 게이트 결합이 전제.

## Re-measurement Procedure
조건 발생 시:

1. `python scripts/measure_similarity_distribution.py` 재실행.
2. `docs/measurements/similarity_distribution_*.md` 갱신.
3. `EpinephrineConfig.similarity_threshold` 값 갱신.
4. `pytest tests/phase1/ tests/phase2/ tests/phase3/` + 100-쿼리 회귀로 영향 확인.
5. 본 ADR의 Status를 Phase 6 도입 시 `Superseded by ADR-N` 로 갱신.

## References
- STEP 3.1 측정 결과: `docs/measurements/similarity_distribution_step3_1.md`
- 측정 raw 데이터: `docs/measurements/similarity_distribution_step3_1.json`
- ADR-001 (latency baseline under multilingual-e5-base)
- ADR-002 (Phase 4 shared embedding pipeline)
- 설계 문서: Neuromodulators 섹션 (에피네프린 발동 조건)
