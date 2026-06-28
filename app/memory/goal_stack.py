"""GoalStack: 세션별 목표 스택 (exponential decay + eviction)."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Final

from app.memory.goal import Goal, GoalStatus

# 상태별 eviction 우선순위 (낮을수록 먼저 제거)
_STATUS_EVICTION_ORDER: Final[dict[str, int]] = {
    "expired": 0,
    "completed": 1,
    "paused": 2,
    "active": 3,
}


@dataclass(frozen=True)
class GoalStackConfig:
    """GoalStack 설정. Phase 6 RPE 도입 시 동적 조정 가능."""

    max_depth: int = 7
    recency_decay_lambda: float = 1.0 / 3600.0  # 1시간 후 약 36.8% 잔존


class GoalStack:
    """세션별 목표 스택.

    - 최대 깊이 7 (configurable)
    - score = priority * exp(-λ * age_seconds)
    - eviction: expired > completed > paused > active, 같은 상태 내 score 낮은 것 우선
    - 단일 asyncio 이벤트 루프 내 가정 (동시 다중 mutation은 PLC 영역)
    """

    def __init__(self, config: GoalStackConfig | None = None) -> None:
        self._config = config or GoalStackConfig()
        self._goals: dict[str, Goal] = {}

    def _effective_score(self, goal: Goal, now: float | None = None) -> float:
        """score = priority * exp(-λ * (now - last_used_at))."""
        current = now if now is not None else time.monotonic()
        age_seconds = max(0.0, current - goal.last_used_at)
        recency_factor = math.exp(-self._config.recency_decay_lambda * age_seconds)
        return goal.priority * recency_factor

    def add(self, goal: Goal) -> Goal | None:
        """Goal 추가. 최대 깊이 초과 시 eviction 발생; 제거된 goal 반환."""
        if goal.goal_id in self._goals:
            raise ValueError(f"Goal already exists: {goal.goal_id}")

        evicted = None
        if len(self._goals) >= self._config.max_depth:
            evicted = self._evict_one()

        self._goals[goal.goal_id] = goal
        return evicted

    def _evict_one(self) -> Goal | None:
        """eviction 후보: 상태 순위 낮은 것, 같은 상태 내 score 낮은 것."""
        if not self._goals:
            return None

        now = time.monotonic()

        def eviction_rank(goal: Goal) -> tuple[int, float]:
            return (_STATUS_EVICTION_ORDER[goal.status], self._effective_score(goal, now))

        _, target_goal = min(self._goals.items(), key=lambda kv: eviction_rank(kv[1]))
        del self._goals[target_goal.goal_id]
        return target_goal

    def get(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def update(self, goal_id: str, **changes: object) -> Goal:
        """Goal 필드 업데이트. updated_at 자동 갱신. last_used_at은 touch()로만."""
        if goal_id not in self._goals:
            raise KeyError(f"Goal not found: {goal_id}")
        old = self._goals[goal_id]
        fields = {**old.model_dump(), **changes, "updated_at": time.monotonic()}
        new_goal = Goal(**fields)
        self._goals[goal_id] = new_goal
        return new_goal

    def touch(self, goal_id: str) -> Goal:
        """last_used_at 갱신 → recency decay 리셋."""
        if goal_id not in self._goals:
            raise KeyError(f"Goal not found: {goal_id}")
        now = time.monotonic()
        old = self._goals[goal_id]
        fields = {**old.model_dump(), "last_used_at": now, "updated_at": now}
        new_goal = Goal(**fields)
        self._goals[goal_id] = new_goal
        return new_goal

    def remove(self, goal_id: str) -> Goal | None:
        return self._goals.pop(goal_id, None)

    def list_all(self) -> list[Goal]:
        return list(self._goals.values())

    def list_by_status(self, status: GoalStatus) -> list[Goal]:
        return [g for g in self._goals.values() if g.status == status]

    def get_active(self) -> list[Goal]:
        """Active goal을 effective_score 내림차순 반환."""
        now = time.monotonic()
        active = [g for g in self._goals.values() if g.status == "active"]
        return sorted(active, key=lambda g: self._effective_score(g, now), reverse=True)

    def get_top_goal(self) -> Goal | None:
        """가장 우선순위 높은 active goal."""
        active = self.get_active()
        return active[0] if active else None

    def __len__(self) -> int:
        return len(self._goals)

    def __contains__(self, goal_id: object) -> bool:
        return goal_id in self._goals
