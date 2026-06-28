"""Phase 5 PFC가 관리하는 단기/중기 목표 데이터 모델."""
from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

GoalStatus = Literal["active", "paused", "completed", "expired"]
GoalSource = Literal["user_explicit", "pfc_inferred", "system"]

_DEFAULT_PRIORITIES: dict[str, float] = {
    "user_explicit": 0.8,
    "pfc_inferred": 0.5,
    "system": 0.3,
}


class Goal(BaseModel):
    """순수 Pydantic 모델. Lock/Queue/DB client/coroutine 객체 포함 금지."""

    goal_id: str
    title: str
    category: str | None = None
    priority: float = Field(ge=0.0, le=1.0, default=0.5)
    status: GoalStatus = "active"
    source: GoalSource = "user_explicit"

    created_at: float
    updated_at: float
    last_used_at: float

    source_trace_id: str | None = None
    session_id: str | None = None

    summary: str | None = None


def make_goal(
    *,
    title: str,
    source: GoalSource,
    session_id: str | None = None,
    source_trace_id: str | None = None,
    priority: float | None = None,
    category: str | None = None,
    summary: str | None = None,
) -> Goal:
    """Goal 생성 헬퍼. source별 기본 priority: user_explicit=0.8, pfc_inferred=0.5, system=0.3."""
    effective_priority = priority if priority is not None else _DEFAULT_PRIORITIES[source]
    now = time.monotonic()
    goal_id = f"goal_{uuid.uuid4().hex[:12]}"
    return Goal(
        goal_id=goal_id,
        title=title,
        category=category,
        priority=effective_priority,
        source=source,
        created_at=now,
        updated_at=now,
        last_used_at=now,
        source_trace_id=source_trace_id,
        session_id=session_id,
        summary=summary,
    )
