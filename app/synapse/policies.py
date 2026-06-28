"""Synapse Flush 정책.

설계서 "맥락 충돌(Flush) 판단 기준": 새 쿼리 임베딩과 직전 관찰
임베딩 간 코사인 유사도가 0.35 미만이면 전체 가중치를 0.3으로 리셋.

decay / saturation 메커니즘은 구현하지 않는다 — 설계서에 명시가 없고,
saturation은 RPE 기반이라 Phase 6 영역.
"""
from __future__ import annotations

import time

import numpy as np

from app.synapse.categories import (
    FLUSH_COSINE_THRESHOLD,
    INITIAL_WEIGHT,
    SYNAPSE_CATEGORIES,
)
from app.synapse.store import SynapseState


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


class FlushPolicy:
    async def should_flush(
        self,
        state: SynapseState,
        new_embedding: list[float],
    ) -> bool:
        """Flush when the new query is semantically far from the last one.

        Returns False when there is nothing to compare against (first
        observation, or either embedding empty) — a missing comparison
        is never grounds for a Flush.
        """
        if not state.last_observed_embedding or not new_embedding:
            return False
        similarity = _cosine(state.last_observed_embedding, new_embedding)
        return similarity < FLUSH_COSINE_THRESHOLD

    async def apply_flush(self, state: SynapseState) -> SynapseState:
        """Reset every weight to 0.3, bump the flush counter + timestamp.

        last_observed_* fields are intentionally left untouched here —
        the caller (SynapseObserver) refreshes them with the new query.
        """
        state.weights = {cat: INITIAL_WEIGHT for cat in sorted(SYNAPSE_CATEGORIES)}
        state.flush_count += 1
        state.last_flush_at = time.time()
        return state
