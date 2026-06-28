"""Phase 5 STEP 2 — IFOMDecision 단위 테스트."""
from __future__ import annotations

import dataclasses
import json

import pytest

from app.memory.ifom import IFOMDecision


def test_ifom_decision_creation():
    d = IFOMDecision(goal_id="g1", action="keep", reason="active_within_ttl", age_seconds=100.0)
    assert d.goal_id == "g1"
    assert d.action == "keep"
    assert d.reason == "active_within_ttl"
    assert d.age_seconds == 100.0


def test_ifom_decision_is_frozen():
    d = IFOMDecision(goal_id="g1", action="keep", reason="active_within_ttl", age_seconds=100.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.action = "remove"  # type: ignore[misc]


@pytest.mark.parametrize("reason", [
    "active_within_ttl",
    "paused_within_ttl",
    "completed_within_ttl",
    "low_priority_kept",
    "already_expired",
])
def test_ifom_decision_keep_reasons(reason: str):
    d = IFOMDecision(goal_id="g1", action="keep", reason=reason, age_seconds=0.0)
    assert d.action == "keep"
    assert d.reason == reason


@pytest.mark.parametrize("reason", [
    "active_ttl_exceeded",
    "paused_ttl_exceeded",
    "low_priority_ttl_exceeded",
])
def test_ifom_decision_mark_expired_reasons(reason: str):
    d = IFOMDecision(goal_id="g1", action="mark_expired", reason=reason, age_seconds=9999.0)
    assert d.action == "mark_expired"
    assert d.reason == reason


def test_ifom_decision_remove_reason():
    d = IFOMDecision(goal_id="g1", action="remove", reason="completed_ttl_exceeded", age_seconds=700.0)
    assert d.action == "remove"
    assert d.reason == "completed_ttl_exceeded"


def test_ifom_decision_asdict():
    d = IFOMDecision(goal_id="g1", action="keep", reason="active_within_ttl", age_seconds=100.0)
    data = dataclasses.asdict(d)
    assert data == {
        "goal_id": "g1",
        "action": "keep",
        "reason": "active_within_ttl",
        "age_seconds": 100.0,
    }


def test_ifom_decision_json_serializable():
    d = IFOMDecision(goal_id="g1", action="remove", reason="completed_ttl_exceeded", age_seconds=700.0)
    data = dataclasses.asdict(d)
    json_str = json.dumps(data)
    loaded = json.loads(json_str)
    assert loaded["action"] == "remove"
    assert loaded["age_seconds"] == 700.0
