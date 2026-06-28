"""Phase 6 STEP 2 — isolation checks for dry-run modules.

All checks are AST-based to catch imports regardless of execution path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RPE_ROOT = Path(__file__).resolve().parents[2] / "app" / "rpe"

FORBIDDEN_PREFIXES = (
    "app.synapse",
    "app.memory",
    "app.routing",
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
    return {p.name: _collect_imports(p) for p in RPE_ROOT.glob("*.py")}


def test_calculators_file_exists() -> None:
    assert (RPE_ROOT / "calculators.py").is_file()


def test_no_forbidden_imports_in_any_rpe_module(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    violations: list[str] = []
    for fname, imports in rpe_module_imports.items():
        for imp in imports:
            for prefix in FORBIDDEN_PREFIXES:
                if imp == prefix or imp.startswith(prefix + "."):
                    violations.append(f"{fname}: {imp}")
    assert violations == [], f"Forbidden imports found: {violations}"


def test_calculators_does_not_import_synapse(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    imports = rpe_module_imports.get("calculators.py", set())
    for imp in imports:
        assert not imp.startswith("app.synapse"), f"calculators.py imports {imp}"


def test_calculators_does_not_import_memory(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    imports = rpe_module_imports.get("calculators.py", set())
    for imp in imports:
        assert not imp.startswith("app.memory"), f"calculators.py imports {imp}"


def test_dopamine_does_not_import_synapse(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    imports = rpe_module_imports.get("dopamine.py", set())
    for imp in imports:
        assert not imp.startswith("app.synapse"), f"dopamine.py imports {imp}"


def test_no_synapse_store_or_state_import(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    for fname, imports in rpe_module_imports.items():
        for imp in imports:
            assert "SynapseStore" not in imp, fname
            assert "SynapseState" not in imp, fname


def test_no_basal_ganglia_references() -> None:
    forbidden_tokens = (
        "BasalGanglia",
        "basal_ganglia",
        "ConflictResolution",
        "conflict_resolution",
    )
    for path in RPE_ROOT.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in src, f"{path.name} references {token}"


def test_no_llm_or_embedder_imports(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    llm_markers = ("anthropic", "openai", "sentence_transformers", "transformers")
    for fname, imports in rpe_module_imports.items():
        for imp in imports:
            for marker in llm_markers:
                assert not imp.startswith(marker), f"{fname} imports {imp}"


def test_no_routes_or_main_imports(
    rpe_module_imports: dict[str, set[str]],
) -> None:
    for fname, imports in rpe_module_imports.items():
        for imp in imports:
            assert imp != "app.main", f"{fname} imports app.main"
            assert "app.api.routes" not in imp, f"{fname} imports routes"
