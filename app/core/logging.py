import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class LogEvent(BaseModel):
    trace_id: str
    module_name: str
    timestamp: datetime
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SpinalLogger:
    _instance: "SpinalLogger | None" = None

    def __new__(cls) -> "SpinalLogger":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._traces = defaultdict(list)
            instance._lock = asyncio.Lock()
            cls._instance = instance
        return cls._instance

    _traces: dict[str, list[LogEvent]]
    _lock: asyncio.Lock

    async def new_trace(self) -> str:
        trace_id = uuid.uuid4().hex
        async with self._lock:
            self._traces[trace_id] = []
        return trace_id

    async def log_event(
        self,
        trace_id: str,
        module_name: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> LogEvent:
        event = LogEvent(
            trace_id=trace_id,
            module_name=module_name,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            payload=payload or {},
        )
        async with self._lock:
            self._traces[trace_id].append(event)
        return event

    def get_trace(self, trace_id: str) -> list[LogEvent]:
        return list(self._traces.get(trace_id, []))


def get_spinal_logger() -> SpinalLogger:
    return SpinalLogger()
