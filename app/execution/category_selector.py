"""Synapse snapshot 기반 카테고리 선택 (ADR-004 규약 1-3단계)."""
from __future__ import annotations

from typing import Final

SYNAPSE_SELECTION_THRESHOLD: Final[float] = 0.4


class CategorySelector:
    """Synapse snapshot 기반 카테고리 선택.

    ADR-004 규약:
      1. synapse_snapshot 참조
      2. 가중치 내림차순 정렬
      3. threshold 이상만 선택
      4. 0개면 evaluator category fallback

    PHASE 6: RPE 도입 후 threshold 재측정 (현재 0.4는 임시값).
    """

    def __init__(self, threshold: float = SYNAPSE_SELECTION_THRESHOLD) -> None:
        self._threshold = threshold

    def select(
        self,
        synapse_snapshot: dict[str, float],
        evaluator_category: str,
        threshold: float | None = None,
    ) -> tuple[list[str], bool]:
        """Return (selected categories ordered by weight desc, fallback_used).

        빈 snapshot(early-exit 경로)이거나 threshold 통과 0개면
        evaluator_category 단독 리스트 + fallback_used=True.

        threshold override (B11 S3b-promote): 주어지면 인스턴스 기본(0.4) 대신 사용한다.
        Epinephrine limit-break가 0.2를 넘겨 거름망을 넓힌다(하한은 호출자가 보장 —
        여기서 0/음수로 무제한 긁기를 강제하지는 않으나 호출자가 0.2 같은 유계값을 준다).
        """
        if not synapse_snapshot:
            return [evaluator_category], True

        effective_threshold = self._threshold if threshold is None else threshold
        sorted_categories = sorted(
            synapse_snapshot.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        selected = [
            cat for cat, weight in sorted_categories
            if weight >= effective_threshold
        ]
        if not selected:
            return [evaluator_category], True
        return selected, False
