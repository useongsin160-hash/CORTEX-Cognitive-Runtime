"""Synapse 카테고리 정의 — 단일 진실 출처.

설계서 LAYER 2.5 SYNAPSE 섹션의 확정 수치.
초기값/상한/하한/Flush 임계값은 RPE 도입 (Phase 6)까지 유지.
단, 가중치 변동은 Phase 3.5에서 Flush 외 작동하지 않음.
"""
from __future__ import annotations

from typing import Final

# 설계서 line 228-234의 7개 카테고리.
SYNAPSE_CATEGORIES: Final[frozenset[str]] = frozenset({
    "coding",
    "game_design",
    "math_logic",
    "writing",
    "data_analysis",
    "system_design",
    "general",
})

INITIAL_WEIGHT: Final[float] = 0.3
WEIGHT_UPPER_BOUND: Final[float] = 1.0
WEIGHT_LOWER_BOUND: Final[float] = 0.1
FLUSH_COSINE_THRESHOLD: Final[float] = 0.35
