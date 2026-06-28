"""GABA 필터 — 의미론적 노이즈 마스킹 (설계서 line 331)."""
from __future__ import annotations

from typing import Final

from app.execution.context_models import RetrievedContext

GABA_SIMILARITY_THRESHOLD: Final[float] = 0.5


class GABAFilter:
    """의미론적 노이즈 필터.

    설계서 line 331: Context Agent가 ChromaDB 검색 후 적용. 코사인
    유사도 편차 초과 데이터를 프롬프트 주입 전 마스킹.

    소프트 필터 정책:
      - similarity >= threshold: accepted
      - similarity <  threshold: masked_by_gaba=True (drop 아님)
      - 전부 masked되면 top-1은 보존 (근거 소실 방지)

    PHASE 6: RPE 도입 후 threshold 재측정 (현재 0.5는 임시값).
    """

    def __init__(self, threshold: float = GABA_SIMILARITY_THRESHOLD) -> None:
        self._threshold = threshold

    def filter(
        self,
        contexts: list[RetrievedContext],
    ) -> tuple[list[RetrievedContext], bool]:
        """Return (filtered list, gaba_fallback_used).

        반환 리스트는 원본과 동일 길이. similarity<threshold 항목은
        masked_by_gaba=True. 전부 masked면 top-1(=index 0, similarity
        내림차순 정렬 가정)만 masked_by_gaba=False로 보존.
        """
        if not contexts:
            return [], False

        filtered = [
            ctx.model_copy(update={
                "masked_by_gaba": ctx.similarity < self._threshold,
            })
            for ctx in contexts
        ]

        if all(ctx.masked_by_gaba for ctx in filtered):
            filtered[0] = filtered[0].model_copy(update={"masked_by_gaba": False})
            return filtered, True

        return filtered, False
