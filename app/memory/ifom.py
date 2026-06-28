"""IFOM: Intent Forgetting / Obsolescence Manager."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.memory.goal import Goal
from app.memory.session_goal_context import SessionGoalContext


@dataclass(frozen=True)
class IFOMConfig:
    """IFOM TTL + threshold 설정. Phase 6 RPE 도입 시 동적 조정 가능."""

    active_ttl_seconds: float = 3600.0       # 60분 sliding TTL
    paused_ttl_seconds: float = 3600.0       # 60분
    completed_ttl_seconds: float = 600.0     # 10분
    low_priority_ttl_seconds: float = 300.0  # 5분
    low_priority_threshold: float = 0.3      # priority <= 0.3 → low priority

    def __post_init__(self) -> None:
        for field_name in (
            "active_ttl_seconds",
            "paused_ttl_seconds",
            "completed_ttl_seconds",
            "low_priority_ttl_seconds",
        ):
            value = getattr(self, field_name)
            if value <= 0:
                raise ValueError(f"{field_name} must be positive: {value}")
        if not (0.0 <= self.low_priority_threshold <= 1.0):
            raise ValueError(
                f"low_priority_threshold must be in [0.0, 1.0]: {self.low_priority_threshold}"
            )


@dataclass(frozen=True)
class IFOMDecision:
    """단일 goal에 대한 IFOM 판단 결과. 관측성과 테스트를 위해 명시적으로 노출."""

    goal_id: str
    action: str       # "keep" / "mark_expired" / "remove"
    reason: str
    age_seconds: float


class IFOMPolicy:
    """Intent Forgetting / Obsolescence Manager.

    - cleanup_expired()를 명시적 메서드로 제공
    - GoalStack의 자동 cleanup 절대 금지
    - PFC 발동 시점에 호출 (STEP 3 이후 연결)
    - Phase 6 STEP 4: ttl_override_resolver optional parameter.
      When provided, adjust_ttl_with_rpe_hook returns session-scoped
      TTL override instead of base_ttl.  Global IFOMConfig is NEVER mutated.

    ttl_override_resolver signature:
        Callable[[str | None, str | None, str], float | None]
        args: (session_id, category, ttl_type)
        ttl_type: one of "active", "paused", "completed", "low_priority"
        returns: override_seconds (float > 0), or None to use base_ttl.

    No direct import of app.rpe.* here — resolver is injected as a Callable.
    """

    def __init__(
        self,
        config: IFOMConfig | None = None,
        ttl_override_resolver: Callable[
            [str | None, str | None, str], float | None
        ] | None = None,
    ) -> None:
        self._config = config or IFOMConfig()
        self._ttl_override_resolver = ttl_override_resolver

    def _is_low_priority(self, goal: Goal) -> bool:
        """priority <= low_priority_threshold."""
        return goal.priority <= self._config.low_priority_threshold

    def _goal_to_ttl_type(self, goal: Goal) -> str:
        """Map goal to IFOMTTLType string for resolver lookup.

        low_priority goals use the "low_priority" TTL type regardless of status.
        Other goals use their status directly (active / paused / completed).
        """
        if self._is_low_priority(goal):
            return "low_priority"
        return goal.status  # "active", "paused", "completed" (not "expired")

    def _get_ttl_for_goal(self, goal: Goal) -> float:
        """Goal 상태와 priority에 따른 TTL 결정.

        expired goal은 이미 처리된 상태이므로 inf.
        low priority는 status와 무관하게 별도 TTL.
        completed + low priority는 min(completed_ttl, low_priority_ttl).
        """
        if goal.status == "expired":
            return float("inf")

        if self._is_low_priority(goal):
            if goal.status == "completed":
                return min(
                    self._config.completed_ttl_seconds,
                    self._config.low_priority_ttl_seconds,
                )
            return self._config.low_priority_ttl_seconds

        ttl_map: dict[str, float] = {
            "active": self._config.active_ttl_seconds,
            "paused": self._config.paused_ttl_seconds,
            "completed": self._config.completed_ttl_seconds,
        }
        return ttl_map.get(goal.status, float("inf"))

    def adjust_ttl_with_rpe_hook(self, goal: Goal, base_ttl: float) -> float:
        """Phase 6 STEP 4: query ttl_override_resolver if provided.

        When ttl_override_resolver is None (default), returns base_ttl unchanged
        (same as Phase 5 STEP 2 no-op behavior).

        When resolver is provided, looks up (session_id, category, ttl_type).
        Returns override if found (float > 0), otherwise base_ttl.
        """
        if self._ttl_override_resolver is not None:
            ttl_type = self._goal_to_ttl_type(goal)
            try:
                override = self._ttl_override_resolver(
                    goal.session_id, goal.category, ttl_type
                )
            except Exception:
                # Resolver errors must not affect IFOM cleanup. Fail-open.
                return base_ttl
            if override is not None and override > 0:
                return override
        return base_ttl

    def evaluate_goal(self, goal: Goal, now: float) -> IFOMDecision:
        """단일 goal에 대한 IFOM 판단.

        Action 매핑:
        - "keep": TTL 미초과
        - "mark_expired": active/paused/low priority TTL 초과 → status=expired
        - "remove": completed TTL 초과 → 즉시 제거

        TTL 초과 후 분기 순서: completed → low_priority → active/paused
        """
        if goal.status == "expired":
            return IFOMDecision(
                goal_id=goal.goal_id,
                action="keep",
                reason="already_expired",
                age_seconds=now - goal.last_used_at,
            )

        base_ttl = self._get_ttl_for_goal(goal)
        adjusted_ttl = self.adjust_ttl_with_rpe_hook(goal, base_ttl)
        age = now - goal.last_used_at

        if age <= adjusted_ttl:
            if self._is_low_priority(goal):
                return IFOMDecision(
                    goal_id=goal.goal_id,
                    action="keep",
                    reason="low_priority_kept",
                    age_seconds=age,
                )
            return IFOMDecision(
                goal_id=goal.goal_id,
                action="keep",
                reason=f"{goal.status}_within_ttl",
                age_seconds=age,
            )

        # TTL 초과: completed → low_priority → active/paused
        if goal.status == "completed":
            return IFOMDecision(
                goal_id=goal.goal_id,
                action="remove",
                reason="completed_ttl_exceeded",
                age_seconds=age,
            )

        if self._is_low_priority(goal):
            return IFOMDecision(
                goal_id=goal.goal_id,
                action="mark_expired",
                reason="low_priority_ttl_exceeded",
                age_seconds=age,
            )

        return IFOMDecision(
            goal_id=goal.goal_id,
            action="mark_expired",
            reason=f"{goal.status}_ttl_exceeded",
            age_seconds=age,
        )

    def cleanup_expired(
        self,
        context: SessionGoalContext,
        now: float | None = None,
    ) -> list[IFOMDecision]:
        """SessionGoalContext의 GoalStack을 명시적으로 정리.

        - now 인자를 받아 테스트 가능성 확보
        - 모든 decision 결정 후 mutation 일괄 적용 (반복 중 dict 변경 회피)
        - GoalStack의 자동 cleanup과 완전히 독립된 명시적 호출
        """
        current = now if now is not None else time.monotonic()

        decisions: list[IFOMDecision] = []
        goals_to_remove: list[str] = []
        goals_to_expire: list[str] = []

        for goal in context.goal_stack.list_all():
            decision = self.evaluate_goal(goal, current)
            decisions.append(decision)
            if decision.action == "remove":
                goals_to_remove.append(goal.goal_id)
            elif decision.action == "mark_expired":
                goals_to_expire.append(goal.goal_id)

        for goal_id in goals_to_remove:
            context.goal_stack.remove(goal_id)

        for goal_id in goals_to_expire:
            context.goal_stack.update(goal_id, status="expired")

        if goals_to_remove or goals_to_expire:
            context.updated_at = current

        return decisions
