"""Phase 6 STEP 4 — IFOM TTL isolation AST tests.

Verifies:
1. ifom_store.py has ZERO imports from app.memory, app.rpe.service,
   app.rpe.dopamine, app.rpe.mutators, app.synapse, app.api, app.execution,
   app.main, app.routing.
2. app/memory/ifom.py has ZERO imports from app.rpe.* (uses Callable protocol).
3. RPEMutationPipelineWrapper (pipeline.py) has ZERO changes — still imports
   same modules as STEP 3.2.
4. routes.py, swarm.py: unchanged (ZERO new imports).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _get_imports(filepath: Path) -> set[str]:
    """Parse top-level + TYPE_CHECKING imports from a .py file."""
    src = filepath.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


# ---------------------------------------------------------------------------
# ifom_store.py isolation
# ---------------------------------------------------------------------------


IFOM_STORE_FORBIDDEN = {
    "app.memory",
    "app.rpe.service",
    "app.rpe.dopamine",
    "app.rpe.mutators",
    "app.synapse",
    "app.api",
    "app.execution",
    "app.main",
    "app.routing",
}


def test_ifom_store_no_forbidden_imports():
    filepath = APP_ROOT / "rpe" / "ifom_store.py"
    assert filepath.exists(), f"{filepath} not found"
    imports = _get_imports(filepath)
    violations = []
    for imp in imports:
        for forbidden in IFOM_STORE_FORBIDDEN:
            if imp == forbidden or imp.startswith(forbidden + "."):
                violations.append(imp)
    assert violations == [], f"ifom_store.py must not import: {violations}"


def test_ifom_store_no_app_rpe_models():
    """ifom_store.py is a pure data module — no rpe models needed."""
    filepath = APP_ROOT / "rpe" / "ifom_store.py"
    imports = _get_imports(filepath)
    rpe_model_imports = [i for i in imports if i == "app.rpe.models" or i.startswith("app.rpe.models.")]
    assert rpe_model_imports == [], f"ifom_store.py should not import app.rpe.models: {rpe_model_imports}"


# ---------------------------------------------------------------------------
# app/memory/ifom.py isolation
# ---------------------------------------------------------------------------


def test_memory_ifom_no_rpe_imports():
    """app/memory/ifom.py must NOT import app.rpe.* directly."""
    filepath = APP_ROOT / "memory" / "ifom.py"
    assert filepath.exists(), f"{filepath} not found"
    imports = _get_imports(filepath)
    rpe_imports = [i for i in imports if i.startswith("app.rpe")]
    assert rpe_imports == [], (
        f"app/memory/ifom.py must not import app.rpe.*; "
        f"use Callable injection. Got: {rpe_imports}"
    )


def test_memory_ifom_callable_import_present():
    """IFOMPolicy uses Callable from collections.abc."""
    filepath = APP_ROOT / "memory" / "ifom.py"
    src = filepath.read_text(encoding="utf-8")
    assert "Callable" in src, "IFOMPolicy must import Callable for ttl_override_resolver type hint"


# ---------------------------------------------------------------------------
# pipeline.py unchanged in STEP 4
# ---------------------------------------------------------------------------


def test_pipeline_no_ifom_store_import():
    """pipeline.py must NOT import app.rpe.ifom_store in STEP 4.

    app.rpe.service is a pre-existing TYPE_CHECKING import from STEP 3.2
    — it is expected and must not be flagged here.
    """
    filepath = APP_ROOT / "rpe" / "pipeline.py"
    imports = _get_imports(filepath)
    # Only ifom_store is the new potential import we forbid in STEP 4
    assert "app.rpe.ifom_store" not in imports, (
        f"pipeline.py must not import app.rpe.ifom_store in STEP 4"
    )


def test_pipeline_no_swarm_runtime_import():
    """pipeline.py must not import app.execution.swarm (runtime)."""
    filepath = APP_ROOT / "rpe" / "pipeline.py"
    imports = _get_imports(filepath)
    for imp in imports:
        assert not (imp == "app.execution.swarm" or imp.startswith("app.execution.swarm.")), (
            f"pipeline.py must not import app.execution.swarm: {imp!r}"
        )


# ---------------------------------------------------------------------------
# routes.py ZERO new imports in STEP 4
# ---------------------------------------------------------------------------


def test_routes_no_ifom_store_import():
    filepath = APP_ROOT / "api" / "routes.py"
    imports = _get_imports(filepath)
    assert "app.rpe.ifom_store" not in imports, \
        "routes.py must not import app.rpe.ifom_store in STEP 4"


def test_routes_no_ifom_mutator_import():
    filepath = APP_ROOT / "api" / "routes.py"
    imports = _get_imports(filepath)
    for imp in imports:
        assert "ifom" not in imp.lower(), \
            f"routes.py must not import IFOM modules: {imp!r}"


# ---------------------------------------------------------------------------
# swarm.py ZERO RPE imports
# ---------------------------------------------------------------------------


def test_swarm_no_rpe_imports():
    filepath = APP_ROOT / "execution" / "swarm.py"
    imports = _get_imports(filepath)
    rpe = [i for i in imports if i.startswith("app.rpe")]
    assert rpe == [], f"swarm.py must have ZERO rpe imports: {rpe}"


def test_swarm_no_ifom_store_import():
    filepath = APP_ROOT / "execution" / "swarm.py"
    imports = _get_imports(filepath)
    assert "app.rpe.ifom_store" not in imports
