"""Phase 6 STEP 3.1 — production pipeline isolation tests.

STEP 3.1 is a SERVICE UNIT only. routes.py and swarm.py must NOT reference
RPEMutationService, SynapseWeightMutator, or DopamineRPE.apply().

STEP 3.2 update: main.py is the DI root and IS allowed to import RPE service
/ mutators (see test_rpe_pipeline_lifespan.py for positive assertions). The
STEP 3.1 isolation test now covers routes.py + swarm.py only.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# STEP 3.2: main.py is the DI root; only routes + swarm remain forbidden.
PIPELINE_FILES = [
    APP_ROOT / "api" / "routes.py",
    APP_ROOT / "execution" / "swarm.py",
]


def _collect_imports_and_names(path: Path) -> tuple[set[str], str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports, src


@pytest.mark.parametrize("path", PIPELINE_FILES, ids=lambda p: p.name)
def test_pipeline_file_does_not_import_rpe_service(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    imports, _ = _collect_imports_and_names(path)
    for imp in imports:
        assert not imp.startswith("app.rpe.service"), f"{path.name} imports {imp}"
        assert not imp.startswith("app.rpe.mutators"), f"{path.name} imports {imp}"


@pytest.mark.parametrize("path", PIPELINE_FILES, ids=lambda p: p.name)
def test_pipeline_file_does_not_reference_active_mutation_symbols(
    path: Path,
) -> None:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    _, src = _collect_imports_and_names(path)
    forbidden_symbols = (
        "RPEMutationService",
        "SynapseWeightMutator",
        "ActiveMutationConfig",
        "RPEMutationRecord",
        "SynapseStoreAdapter",
    )
    for symbol in forbidden_symbols:
        assert symbol not in src, (
            f"{path.name} references {symbol} — must go via rpe_pipeline"
        )


@pytest.mark.parametrize("path", PIPELINE_FILES, ids=lambda p: p.name)
def test_pipeline_file_does_not_call_dopamine_apply(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    src = path.read_text(encoding="utf-8")
    # DopamineRPE.apply() must only be called from pipeline.py.
    assert ".apply(" not in src or "DopamineRPE" not in src, (
        f"{path.name} may call DopamineRPE.apply() — must go via rpe_pipeline"
    )


def test_main_lifespan_has_rpe_pipeline_di() -> None:
    """STEP 3.2: main.py IS the DI root — verifies rpe_pipeline is wired."""
    main_path = APP_ROOT / "main.py"
    if not main_path.is_file():
        pytest.skip("main.py not present")
    src = main_path.read_text(encoding="utf-8")
    # STEP 3.2 adds RPE pipeline DI to main.py (disabled-by-default).
    assert "RPEMutationService" in src, "main.py must create RPEMutationService"
    assert "rpe_pipeline" in src, "main.py must assign app.state.rpe_pipeline"
    assert "enabled=False" in src, "RPEMutationConfig must default to disabled"


def test_no_background_task_for_rpe_apply_outside_rpe_and_main() -> None:
    """Background RPE tasks are confined to app/rpe/ and main.py (DI root).

    routes.py, swarm.py, and all other app/ modules must NOT reference
    RPEMutationService directly.
    """
    for path in APP_ROOT.rglob("*.py"):
        if "rpe" in path.parts or path.name.startswith("test_"):
            continue
        # main.py is the DI root (STEP 3.2) — allowed to reference RPE
        if path.name == "main.py":
            continue
        src = path.read_text(encoding="utf-8")
        if "RPEMutationService" in src:
            pytest.fail(
                f"{path.relative_to(APP_ROOT)} references RPEMutationService "
                f"outside app/rpe/ (must go via rpe_pipeline)"
            )
