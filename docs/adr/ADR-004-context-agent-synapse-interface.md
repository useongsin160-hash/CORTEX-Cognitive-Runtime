# ADR-004: Context Agent ↔ Synapse 인터페이스 규약

## Status
Proposed (Phase 4 진입 시 본격 발동)

## Context
설계서 line 277-280, line 456 명세:
- Context Agent는 Phase 4 Execution Layer 소속
- Synapse 가중치를 TaskContext에서 참조하여 ChromaDB 탐색 범위 선결정
- 탐색 순서: Synapse 가중치 확보 → ChromaDB 탐색

Phase 3.5에서 Synapse Layer를 구축했고, TaskContext.synapse_snapshot에
가중치 dict 탑재 인프라가 완성됨. Context Agent는 Phase 4에서 구현되므로
Phase 3.5 시점에는 인터페이스 규약만 고정한다.

## Decision

Context Agent는 다음 순서를 엄수한다:

1. TaskContext.synapse_snapshot 참조
   - 7개 카테고리 → 가중치 float 매핑
   - 빈 dict면 early-exit 경로이므로 Synapse 미사용

2. 가중치 정렬
   - synapse_snapshot.items() 를 weight 기준 내림차순 정렬
   - 최고 가중치 카테고리부터 우선 탐색

3. ChromaDB 탐색 범위 결정
   - 최고 가중치 카테고리의 관련 컬렉션 우선 검색
   - 가중치가 threshold (예: 0.5) 이상인 카테고리만 탐색
   - threshold 미만 카테고리는 fallback 시에만 탐색

4. GABA 필터 적용 (설계서 line 331)
   - ChromaDB 검색 결과 중 코사인 유사도 편차 초과 데이터 마스킹
   - 프롬프트 주입 전 처리

## Interface Specification

Context Agent 메서드 시그니처 (Phase 4 구현 시 준수):

```python
async def search_context(
    self,
    task_context: TaskContext,
) -> ContextResult:
    """
    TaskContext.synapse_snapshot 참조 후 ChromaDB 탐색.

    반환:
    - ContextResult: 검색된 컨텍스트 + GABA 필터링 결과
    """
```

## Consequences

긍정적:
- Phase 3.5와 Phase 4 사이 인터페이스 명확
- Context Agent 구현 시 갈팡질팡 없음
- 미래 Synapse 가중치 변동 (Phase 6 RPE)이 자동으로 Context Agent 영향

부정적:
- Context Agent 구현 시 본 ADR 준수 강제
- threshold 0.5는 임시값 (Phase 6 RPE 도입 후 재측정 필요)

## Dependencies
- Phase 3.5 STEP 1 완료 (TaskContext.synapse_snapshot 인프라)
- Phase 4 진입 시 본격 구현
- Phase 6 RPE 도입 시 threshold 재측정

## References
- 설계서 LAYER 3 EXECUTION 섹션
- 설계서 line 456 (Context Agent 탐색 순서)
- ADR-002 (Phase 4 shared embedding pipeline)
- ADR-003 (epinephrine threshold)
- PHASE3_5_COMPLETE.md
