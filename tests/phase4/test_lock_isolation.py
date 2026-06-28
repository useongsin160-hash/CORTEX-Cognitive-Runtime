"""Phase 4 STEP 4 — Dependency isolation tests.

Verifies:
- LockManager does not import routing or execution modules
- PLC does not import routes.py (api layer)
- LockManager instance is not stored inside TaskContext
- TaskContext has no lock-typed fields
"""
from __future__ import annotations

import importlib
import inspect
import sys

import pytest

from app.api.schemas.context import TaskContext
from app.core.lock_manager import LockManager, LockType


# ---------------------------------------------------------------------------
# LockManager import isolation (core dependency rule)
# ---------------------------------------------------------------------------

def test_lock_manager_module_does_not_import_routing():
    import app.core.lock_manager as lm_mod

    source = inspect.getsource(lm_mod)
    assert "app.routing" not in source, (
        "LockManager must not import from app.routing (core dependency rule)"
    )


def test_lock_manager_module_does_not_import_execution():
    import app.core.lock_manager as lm_mod

    source = inspect.getsource(lm_mod)
    assert "app.execution" not in source, (
        "LockManager must not import from app.execution (core dependency rule)"
    )


def test_lock_manager_module_does_not_import_api_routes():
    import app.core.lock_manager as lm_mod

    source = inspect.getsource(lm_mod)
    assert "app.api.routes" not in source


# ---------------------------------------------------------------------------
# PLC import isolation
# ---------------------------------------------------------------------------

def test_plc_module_does_not_import_api_routes():
    import app.maintenance.plc as plc_mod

    source = inspect.getsource(plc_mod)
    assert "app.api.routes" not in source, (
        "PLC must not import routes.py"
    )


def test_plc_module_does_not_import_execution():
    import app.maintenance.plc as plc_mod

    source = inspect.getsource(plc_mod)
    assert "app.execution" not in source


# ---------------------------------------------------------------------------
# TaskContext must not carry LockManager instances
# ---------------------------------------------------------------------------

def test_task_context_does_not_hold_lock_manager():
    lm = LockManager()
    tc = TaskContext(trace_id="t1")

    # Attempting to set lock_manager on TaskContext must fail (Pydantic
    # forbids extra fields by default) or at minimum not persist.
    try:
        tc.lock_manager = lm  # type: ignore[attr-defined]
    except Exception:
        pass

    assert not hasattr(tc, "lock_manager") or getattr(tc, "lock_manager", None) is None, (
        "LockManager must not be stored inside TaskContext"
    )


def test_task_context_fields_contain_no_lock_type():
    """TaskContext model fields must be pure data — no asyncio.Lock types."""
    for field_name, field_info in TaskContext.model_fields.items():
        annotation = field_info.annotation
        annotation_str = str(annotation)
        assert "Lock" not in annotation_str, (
            f"TaskContext.{field_name} annotation contains 'Lock': {annotation_str}"
        )


# ---------------------------------------------------------------------------
# External singleton pattern: LockManager lives outside TaskContext
# ---------------------------------------------------------------------------

def test_lock_manager_is_external_singleton():
    """LockManager is instantiated externally and injected; never inside TC."""
    lm1 = LockManager()
    lm2 = LockManager()
    # Two separate instances: external creation is possible
    assert lm1 is not lm2

    tc = TaskContext(trace_id="t1")
    # TaskContext must not spontaneously create a LockManager
    assert not hasattr(tc, "_lock_manager")
