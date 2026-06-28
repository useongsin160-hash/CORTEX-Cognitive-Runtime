"""Phase 5 STEP 4 — PFCIntegrationConfig 단위 테스트."""
from __future__ import annotations

import dataclasses

import pytest

from app.routing.pfc import PFCIntegrationConfig


# ---------------------------------------------------------------------------
# 기본값
# ---------------------------------------------------------------------------


def test_default_hint_timeout_ms():
    cfg = PFCIntegrationConfig()
    assert cfg.hint_timeout_ms == 30.0


def test_default_max_hint_timeout_ms():
    cfg = PFCIntegrationConfig()
    assert cfg.max_hint_timeout_ms == 50.0


def test_default_confidence_threshold():
    cfg = PFCIntegrationConfig()
    assert cfg.pfc_confidence_threshold == 0.7


def test_max_timeout_ge_hint_timeout_invariant():
    cfg = PFCIntegrationConfig()
    assert cfg.max_hint_timeout_ms >= cfg.hint_timeout_ms


# ---------------------------------------------------------------------------
# frozen
# ---------------------------------------------------------------------------


def test_config_is_frozen():
    cfg = PFCIntegrationConfig()
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        cfg.hint_timeout_ms = 100.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------


def test_zero_hint_timeout_raises():
    with pytest.raises(ValueError, match="hint_timeout_ms"):
        PFCIntegrationConfig(hint_timeout_ms=0.0)


def test_negative_hint_timeout_raises():
    with pytest.raises(ValueError, match="hint_timeout_ms"):
        PFCIntegrationConfig(hint_timeout_ms=-1.0)


def test_max_timeout_less_than_hint_raises():
    with pytest.raises(ValueError, match="max_hint_timeout_ms"):
        PFCIntegrationConfig(hint_timeout_ms=50.0, max_hint_timeout_ms=30.0)


def test_confidence_threshold_below_zero_raises():
    with pytest.raises(ValueError, match="pfc_confidence_threshold"):
        PFCIntegrationConfig(pfc_confidence_threshold=-0.01)


def test_confidence_threshold_above_one_raises():
    with pytest.raises(ValueError, match="pfc_confidence_threshold"):
        PFCIntegrationConfig(pfc_confidence_threshold=1.01)


def test_confidence_threshold_boundary_zero_ok():
    cfg = PFCIntegrationConfig(pfc_confidence_threshold=0.0)
    assert cfg.pfc_confidence_threshold == 0.0


def test_confidence_threshold_boundary_one_ok():
    cfg = PFCIntegrationConfig(pfc_confidence_threshold=1.0)
    assert cfg.pfc_confidence_threshold == 1.0


def test_max_equals_hint_timeout_ok():
    cfg = PFCIntegrationConfig(hint_timeout_ms=30.0, max_hint_timeout_ms=30.0)
    assert cfg.hint_timeout_ms == cfg.max_hint_timeout_ms == 30.0


# ---------------------------------------------------------------------------
# 사용자 지정
# ---------------------------------------------------------------------------


def test_custom_values():
    cfg = PFCIntegrationConfig(
        hint_timeout_ms=15.0,
        max_hint_timeout_ms=40.0,
        pfc_confidence_threshold=0.5,
    )
    assert cfg.hint_timeout_ms == 15.0
    assert cfg.max_hint_timeout_ms == 40.0
    assert cfg.pfc_confidence_threshold == 0.5
