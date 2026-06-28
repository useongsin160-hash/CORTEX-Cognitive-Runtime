"""Phase 4 STEP 5.1 — Glycine dependency isolation tests.

Glycine lives in app.routing.neuromodulators — it must not import:
  - app.execution (swarm / factory)
  - app.api.routes
  - app.core.lock_manager (LockManager)
  - app.maintenance.plc (PLC)
"""
from __future__ import annotations

import inspect

import app.routing.neuromodulators as neuro_module


def _import_lines(src: str) -> str:
    return "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    )


def test_neuromodulators_does_not_import_swarm():
    """Glycine must not depend on the swarm / factory layer."""
    imports = _import_lines(inspect.getsource(neuro_module))
    assert "app.execution.swarm" not in imports
    assert "app.execution.factory" not in imports


def test_neuromodulators_does_not_import_api_routes():
    imports = _import_lines(inspect.getsource(neuro_module))
    assert "app.api.routes" not in imports, (
        "neuromodulators must not import routes.py"
    )


def test_neuromodulators_does_not_import_lock_manager():
    imports = _import_lines(inspect.getsource(neuro_module))
    assert "lock_manager" not in imports, (
        "Glycine must not depend on LockManager"
    )


def test_neuromodulators_does_not_import_plc():
    imports = _import_lines(inspect.getsource(neuro_module))
    assert "app.maintenance.plc" not in imports, (
        "Glycine must not depend on PLC"
    )


def test_neuromodulators_does_not_import_vendor_llm_sdk():
    imports = _import_lines(inspect.getsource(neuro_module))
    for sdk in ("import anthropic", "import openai", "from anthropic", "from openai"):
        assert sdk not in imports


def test_glycine_config_is_dataclass():
    from app.routing.neuromodulators import GlycineConfig
    import dataclasses
    assert dataclasses.is_dataclass(GlycineConfig)


def test_glycine_decision_is_dataclass():
    from app.routing.neuromodulators import GlycineDecision
    import dataclasses
    assert dataclasses.is_dataclass(GlycineDecision)
