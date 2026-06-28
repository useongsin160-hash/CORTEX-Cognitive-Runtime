"""Skip-Logic Router.

Replaces the legacy 14-stage fixed loop with a difficulty-driven branch
table. Pure decision logic — execution belongs to downstream layers.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.schemas.context import Difficulty, TaskContext


class RouteDecision(BaseModel):
    path: str
    skip_layers: list[str] = Field(default_factory=list)
    reason: str


# B12 — 5-stage difficulty grouped into 3 physical paths:
#   {1}=lightweight, {2,3}=standard, {4,5}=full_pipeline.
# NE boost applies on the full_pipeline band (difficulty >= VERY_HARD).
_DECISIONS: dict[Difficulty, tuple[str, list[str], str]] = {
    Difficulty.EASY: (
        "lightweight",
        ["full_planner", "basal_ganglia_cr"],
        "difficulty 1 — Tier-1.5 branch may apply downstream",
    ),
    Difficulty.MEDIUM: (
        "standard",
        [],
        "difficulty 2 — standard pipeline",
    ),
    Difficulty.HARD: (
        "standard",
        [],
        "difficulty 3 — standard pipeline",
    ),
    Difficulty.VERY_HARD: (
        "full_pipeline",
        [],
        "difficulty 4 — full async swarm with NE boost",
    ),
    Difficulty.DEEP_THINKING: (
        "full_pipeline",
        [],
        "difficulty 5 — full async swarm with NE boost",
    ),
}


class SkipLogicRouter:
    async def route(self, task_context: TaskContext) -> RouteDecision:
        path, skip_layers, reason = _DECISIONS[task_context.difficulty]
        return RouteDecision(path=path, skip_layers=skip_layers, reason=reason)
