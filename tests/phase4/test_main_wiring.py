"""Phase 4 STEP 5.1 — app.state wiring verification.

Confirms that all Phase 4 components are present and properly connected
on app.state after create_app() runs.
"""
from __future__ import annotations

import pytest

from app.core.lock_manager import LockManager
from app.execution.swarm import AsyncSwarm
from app.main import app
from app.maintenance.plc import PLC
from app.routing.lc import LocusCoeruleus
from app.routing.neuromodulators import Glycine


# ---------------------------------------------------------------------------
# All required state attributes exist
# ---------------------------------------------------------------------------

def test_app_state_has_lock_manager():
    assert hasattr(app.state, "lock_manager")
    assert isinstance(app.state.lock_manager, LockManager)


def test_app_state_has_plc():
    assert hasattr(app.state, "plc")
    assert isinstance(app.state.plc, PLC)


def test_app_state_has_async_swarm():
    assert hasattr(app.state, "async_swarm")
    assert isinstance(app.state.async_swarm, AsyncSwarm)


def test_app_state_has_lc():
    assert hasattr(app.state, "lc")
    assert isinstance(app.state.lc, LocusCoeruleus)


def test_app_state_has_glycine():
    assert hasattr(app.state, "glycine")
    assert isinstance(app.state.glycine, Glycine)


# ---------------------------------------------------------------------------
# Dependency wiring correctness
# ---------------------------------------------------------------------------

def test_plc_wraps_app_lock_manager():
    """PLC._lock_manager must be the same object as app.state.lock_manager."""
    assert app.state.plc._lock_manager is app.state.lock_manager


def test_lc_holds_app_lock_manager():
    """LC._lock_manager must be the same object as app.state.lock_manager."""
    assert app.state.lc._lock_manager is app.state.lock_manager


def test_glycine_has_default_config():
    from app.routing.neuromodulators import GlycineConfig
    assert isinstance(app.state.glycine._config, GlycineConfig)
    assert app.state.glycine._config.token_budget == 4000
    assert app.state.glycine._config.rate_max_requests == 30
    assert app.state.glycine._config.loop_threshold == 5
