"""Phase 6 STEP 1 — RPE module isolation tests.

Verify that app/rpe/* does NOT import forbidden modules. These checks
are AST-based so they catch imports regardless of execution path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RPE_ROOT = Path(__file__).resolve().parents[2] / "app" / "rpe"

FORBIDDEN_PREFIXES = (
    "app.memory.ifom",
    "app.routing.pfc",
    "app.api.routes",
    "app.main",
    "app.execution.basal_ganglia",
    "app.basal_ganglia",
    "app.cr",
    "app.conflict_resolution",
    "legacy",
    "sentence_transformers",
    "transformers",
    "anthropic",
    "openai",
)


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


@pytest.fixture(scope="module")
def rpe_module_imports() -> dict[str, set[str]]:
    return {
        p.name: _collect_imports(p)
        for p in RPE_ROOT.glob("*.py")
    }


def test_rpe_root_exists() -> None:
    assert RPE_ROOT.is_dir()
    files = {p.name for p in RPE_ROOT.glob("*.py")}
    # STEP 1 files + STEP 2 calculators.py
    expected = {"__init__.py", "models.py", "sources.py", "dopamine.py", "calculators.py"}
    assert expected.issubset(files)


def test_no_forbidden_imports(rpe_module_imports: dict[str, set[str]]) -> None:
    violations: list[str] = []
    for fname, imports in rpe_module_imports.items():
        for imp in imports:
            for prefix in FORBIDDEN_PREFIXES:
                if imp == prefix or imp.startswith(prefix + "."):
                    violations.append(f"{fname}: {imp}")
    assert violations == [], "RPE module must not import forbidden modules"


def test_only_safe_app_imports(rpe_module_imports: dict[str, set[str]]) -> None:
    allowed_app_modules = {
        "app.core.logging",
        "app.rpe.models",
        "app.rpe.sources",
        "app.rpe.calculators",  # STEP 2: dopamine.py imports calculators
        "app.rpe.mutators",  # STEP 3.1: service.py imports mutators
        "app.rpe.service",  # STEP 3.1: dopamine.py TYPE_CHECKING import
        # STEP 3.2: pipeline.py connector layer
        "app.rpe.dopamine",           # pipeline.py TYPE_CHECKING import
        "app.api.schemas.context",    # pipeline.py: TaskContext
        "app.api.schemas.query_features",  # pipeline.py: QueryFeatures
        "app.execution.swarm_models",  # pipeline.py: SwarmResult (pure data)
        # STEP 4: IFOM TTL override store (pure data, no memory/api imports)
        "app.rpe.ifom_store",  # calculators.py, mutators.py, dopamine.py, service.py
        # B11 S1: category×difficulty store (pure data — only imports typing)
        "app.rpe.difficulty_store",  # calculators.py, mutators.py
        # B11 S2: difficulty learner (RPE-internal orchestration only)
        "app.rpe.difficulty_learner",  # pipeline.py TYPE_CHECKING import
        # B3a: aiosqlite record persistence (pure DB infra, no memory/api/policy)
        "app.rpe.record_store",  # service.py TYPE_CHECKING import
        "app.core.errors",       # record_store.py / preset_store.py: DatabaseError
        "app.db.sqlite",         # record_store.py / preset_store.py: _normalize_path
        # B3b: global EMA preset + presetted difficulty store (pure DB infra)
        "app.rpe.preset_store",  # service.py TYPE_CHECKING import
        # B4: auto-rollback scheduler (apscheduler wrapper, no memory/api/policy)
        "app.rpe.rollback_scheduler",  # service.py TYPE_CHECKING import
        # B10: read-side recent-outcome counter (pure data — only imports collections)
        "app.rpe.recent_counter",  # pipeline.py TYPE_CHECKING import
    }
    violations: list[str] = []
    for fname, imports in rpe_module_imports.items():
        for imp in imports:
            if imp.startswith("app.") and imp not in allowed_app_modules:
                violations.append(f"{fname}: {imp}")
    assert violations == [], (
        f"RPE module may only import {sorted(allowed_app_modules)}; "
        f"got: {violations}"
    )


def test_no_basal_ganglia_or_cr_references() -> None:
    forbidden_tokens = (
        "BasalGanglia",
        "basal_ganglia",
        "ConflictResolution",
        "conflict_resolution",
    )
    for path in RPE_ROOT.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in src, f"{path.name} must not reference {token}"
