"""SessionGoalStore Protocol + InMemorySessionGoalStore 구현."""
from __future__ import annotations

from typing import Protocol

from app.memory.goal_stack import GoalStackConfig
from app.memory.session_goal_context import SessionGoalContext


class SessionGoalStore(Protocol):
    """세션별 goal 컨텍스트 저장소 인터페이스.

    Phase 5 STEP 1: InMemorySessionGoalStore만 구현.
    SQLite/Chroma 영속화는 Phase 5 후반 또는 후속 Phase.
    Phase 5 STEP 2 (IFOM)에서 TTL 정책 적용 예정.
    """

    async def get_or_create_session(self, session_id: str) -> SessionGoalContext: ...

    async def get_or_create_trace(self, trace_id: str) -> SessionGoalContext: ...

    async def delete_session(self, session_id: str) -> bool: ...

    async def delete_trace(self, trace_id: str) -> bool: ...

    async def list_sessions(self) -> list[str]: ...

    async def list_traces(self) -> list[str]: ...


class InMemorySessionGoalStore:
    """In-memory 구현.

    - session_id 스코프와 trace_id 스코프를 별도 dict로 분리
    - trace는 휘발성 강조 (Phase 5 STEP 2 IFOM cleanup 예정)
    """

    def __init__(self, config: GoalStackConfig | None = None) -> None:
        self._config = config
        self._sessions: dict[str, SessionGoalContext] = {}
        self._traces: dict[str, SessionGoalContext] = {}

    async def get_or_create_session(self, session_id: str) -> SessionGoalContext:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionGoalContext.for_session(
                session_id, self._config
            )
        return self._sessions[session_id]

    async def get_or_create_trace(self, trace_id: str) -> SessionGoalContext:
        if trace_id not in self._traces:
            self._traces[trace_id] = SessionGoalContext.for_trace(
                trace_id, self._config
            )
        return self._traces[trace_id]

    async def delete_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    async def delete_trace(self, trace_id: str) -> bool:
        return self._traces.pop(trace_id, None) is not None

    async def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def list_traces(self) -> list[str]:
        return list(self._traces.keys())
