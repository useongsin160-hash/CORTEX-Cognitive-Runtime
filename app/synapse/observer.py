"""Synapse Observe 단계 — Evaluator 결과 관찰/기록.

설계 규약: Observe는 라우팅 결정을 절대 변경하지 않는다. 세션별
카테고리 누적 기록 + Flush 판단만 수행한다.
"""
from __future__ import annotations

from app.core.logging import get_spinal_logger
from app.synapse.policies import FlushPolicy
from app.synapse.store import SynapseStore


class SynapseObserver:
    def __init__(self, store: SynapseStore, flush_policy: FlushPolicy) -> None:
        self._store = store
        self._flush_policy = flush_policy

    async def observe(
        self,
        session_id: str,
        category: str,
        embedding: list[float],
        similarity: float,
        trace_id: str | None = None,
    ) -> None:
        """Record an Evaluator result into the session's synapse state.

        Steps:
          1. load the session state,
          2. Flush check vs. the previous observation's embedding —
             on Flush, every weight resets to 0.3,
          3. refresh last_observed_* fields,
          4. weights themselves are NOT mutated here (Phase 6 RPE).

        trace_id, when supplied, anchors the synapse.observed /
        synapse.flushed Spinal events.
        """
        logger = get_spinal_logger()
        state = await self._store.get_state(session_id)

        flushed = await self._flush_policy.should_flush(state, embedding)
        if flushed:
            await self._flush_policy.apply_flush(state)
            if trace_id is not None:
                await logger.log_event(
                    trace_id=trace_id,
                    module_name="synapse.observer",
                    event_type="synapse.flushed",
                    payload={
                        "session_id": session_id,
                        "flush_count": state.flush_count,
                    },
                )

        state.last_observed_category = category
        state.last_observed_similarity = similarity
        state.last_observed_embedding = list(embedding)
        await self._store.update_state(session_id, state)

        if trace_id is not None:
            await logger.log_event(
                trace_id=trace_id,
                module_name="synapse.observer",
                event_type="synapse.observed",
                payload={
                    "session_id": session_id,
                    "category": category,
                    "similarity": similarity,
                    "flushed": flushed,
                },
            )
