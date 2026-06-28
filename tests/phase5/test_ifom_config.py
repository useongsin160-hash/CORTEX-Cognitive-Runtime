"""Phase 5 STEP 2 — IFOMConfig 단위 테스트."""
from __future__ import annotations

import dataclasses
import json

import pytest

from app.memory.ifom import IFOMConfig


def test_ifom_config_default_active_ttl():
    assert IFOMConfig().active_ttl_seconds == 3600.0


def test_ifom_config_default_paused_ttl():
    assert IFOMConfig().paused_ttl_seconds == 3600.0


def test_ifom_config_default_completed_ttl():
    assert IFOMConfig().completed_ttl_seconds == 600.0


def test_ifom_config_default_low_priority_ttl():
    assert IFOMConfig().low_priority_ttl_seconds == 300.0


def test_ifom_config_default_threshold():
    assert IFOMConfig().low_priority_threshold == 0.3


def test_ifom_config_is_frozen():
    cfg = IFOMConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.active_ttl_seconds = 100.0  # type: ignore[misc]


def test_ifom_config_ttl_zero_raises():
    with pytest.raises(ValueError, match="must be positive"):
        IFOMConfig(active_ttl_seconds=0.0)


def test_ifom_config_ttl_negative_raises():
    with pytest.raises(ValueError, match="must be positive"):
        IFOMConfig(paused_ttl_seconds=-1.0)


def test_ifom_config_completed_ttl_zero_raises():
    with pytest.raises(ValueError, match="must be positive"):
        IFOMConfig(completed_ttl_seconds=0.0)


def test_ifom_config_low_priority_ttl_zero_raises():
    with pytest.raises(ValueError, match="must be positive"):
        IFOMConfig(low_priority_ttl_seconds=0.0)


def test_ifom_config_threshold_below_zero_raises():
    with pytest.raises(ValueError, match="low_priority_threshold"):
        IFOMConfig(low_priority_threshold=-0.01)


def test_ifom_config_threshold_above_one_raises():
    with pytest.raises(ValueError, match="low_priority_threshold"):
        IFOMConfig(low_priority_threshold=1.01)


def test_ifom_config_threshold_boundary_zero_valid():
    cfg = IFOMConfig(low_priority_threshold=0.0)
    assert cfg.low_priority_threshold == 0.0


def test_ifom_config_threshold_boundary_one_valid():
    cfg = IFOMConfig(low_priority_threshold=1.0)
    assert cfg.low_priority_threshold == 1.0


def test_ifom_config_custom_values():
    cfg = IFOMConfig(
        active_ttl_seconds=7200.0,
        paused_ttl_seconds=1800.0,
        completed_ttl_seconds=120.0,
        low_priority_ttl_seconds=60.0,
        low_priority_threshold=0.2,
    )
    assert cfg.active_ttl_seconds == 7200.0
    assert cfg.paused_ttl_seconds == 1800.0
    assert cfg.completed_ttl_seconds == 120.0
    assert cfg.low_priority_ttl_seconds == 60.0
    assert cfg.low_priority_threshold == 0.2


def test_ifom_config_fields_are_primitive():
    cfg = IFOMConfig()
    d = dataclasses.asdict(cfg)
    for k, v in d.items():
        assert isinstance(v, float), f"{k} should be float"


def test_ifom_config_json_serializable():
    cfg = IFOMConfig()
    data = dataclasses.asdict(cfg)
    json_str = json.dumps(data)
    loaded = json.loads(json_str)
    assert loaded["active_ttl_seconds"] == 3600.0
    assert loaded["low_priority_threshold"] == 0.3
