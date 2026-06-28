"""Synapse Snapshot 단계 — Tier-1.5 miss 이후 가중치 추출.

추출 결과는 TaskContext.synapse_snapshot 필드에 그대로 할당 가능한
순수 dict[str, float] — JSON 직렬화 안전.
"""
from __future__ import annotations

from app.synapse.store import SynapseStore


class SynapseSnapshotter:
    def __init__(self, store: SynapseStore) -> None:
        self._store = store

    async def take_snapshot(self, session_id: str) -> dict[str, float]:
        """Return the session's 7-category weight map as a plain dict.

        Safe to assign directly onto TaskContext.synapse_snapshot.
        """
        return await self._store.snapshot(session_id)
