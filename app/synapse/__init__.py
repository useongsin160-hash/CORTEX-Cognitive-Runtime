"""Layer 2.5 — Synapse Layer (Phase 3.5).

카테고리 연쇄 가중치 레이어. ChromaDB(해마)와 분리된 독립 저장소로,
단기~중기 라우팅 가중치 맵을 관리한다.

Phase 3.5 범위: Observe + Snapshot 인프라.
  - Observe: Evaluator 결과 관찰/기록 (라우팅 결정 변경 금지)
  - Snapshot: Tier-1.5 miss 이후 가중치 추출 → TaskContext 탑재
  - Use (Phase 4): Context Agent가 snapshot 참조 — 본 Phase에서 미구현
"""
