"""SessionGoalContext: 세션 또는 trace 범위 goal 컨텍스트."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.memory.goal import Goal
from app.memory.goal_stack import GoalStack, GoalStackConfig


@dataclass
class SessionGoalContext:
    """세션(session_id) 또는 trace(trace_id) 범위 goal 컨텍스트.

    규약:
    - GoalStack 보유 (외부 Store가 생명주기 관리)
    - session 스코프: 장기 누적 가능
    - trace 스코프: ephemeral, 장기 누적 금지
    """

    scope_id: str
    scope_type: str  # "session" 또는 "trace"
    goal_stack: GoalStack
    created_at: float
    updated_at: float
    last_active_goal_id: str | None = field(default=None)

    @classmethod
    def for_session(
        cls,
        session_id: str,
        config: GoalStackConfig | None = None,
    ) -> SessionGoalContext:
        now = time.monotonic()
        return cls(
            scope_id=session_id,
            scope_type="session",
            goal_stack=GoalStack(config),
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def for_trace(
        cls,
        trace_id: str,
        config: GoalStackConfig | None = None,
    ) -> SessionGoalContext:
        """session_id 없는 요청용 ephemeral 컨텍스트. 장기 누적 금지."""
        now = time.monotonic()
        return cls(
            scope_id=trace_id,
            scope_type="trace",
            goal_stack=GoalStack(config),
            created_at=now,
            updated_at=now,
        )

    def add_goal(self, goal: Goal) -> Goal | None:
        """Goal 추가. eviction된 goal 반환 (있을 경우)."""
        evicted = self.goal_stack.add(goal)
        self.updated_at = time.monotonic()
        return evicted

    def set_active(self, goal_id: str) -> None:
        """지정 goal을 active로 설정. 기존 active는 모두 paused로 전환."""
        if goal_id not in self.goal_stack:
            raise KeyError(f"Goal not found in stack: {goal_id}")

        for goal in self.goal_stack.list_by_status("active"):
            if goal.goal_id != goal_id:
                self.goal_stack.update(goal.goal_id, status="paused")

        self.goal_stack.update(goal_id, status="active")
        self.goal_stack.touch(goal_id)

        self.last_active_goal_id = goal_id
        self.updated_at = time.monotonic()

    def get_active_goal(self) -> Goal | None:
        """현재 active goal 반환. last_active_goal_id stale 시 get_top_goal fallback."""
        if self.last_active_goal_id:
            goal = self.goal_stack.get(self.last_active_goal_id)
            if goal and goal.status == "active":
                return goal
        return self.goal_stack.get_top_goal()
