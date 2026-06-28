"""Phase 6 STEP 3.1 — active mutation isolation tests.

STEP 3.1 relaxes app.synapse isolation (mutators may import SynapseStore).
All other prohibitions remain.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RPE_ROOT = Path(__file__).resolve().parents[2] / "app" / "rpe"

# These prefixes remain forbidden everywhere in app/rpe.
GLOBAL_FORBIDDEN_PREFIXES = (
    "app.memory",
    "app.routing",
    "app.api.routes",
    "app.main",
    "app.execution.swarm",
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
def rpe_imports() -> dict[str, set[str]]:
    return {p.name: _collect_imports(p) for p in RPE_ROOT.glob("*.py")}


def test_step3_1_files_exist() -> None:
    assert (RPE_ROOT / "service.py").is_file()
    assert (RPE_ROOT / "mutators.py").is_file()


def test_step3_2_pipeline_file_exists() -> None:
    assert (RPE_ROOT / "pipeline.py").is_file()


def test_no_globally_forbidden_imports(rpe_imports: dict[str, set[str]]) -> None:
    violations: list[str] = []
    for fname, imports in rpe_imports.items():
        for imp in imports:
            for prefix in GLOBAL_FORBIDDEN_PREFIXES:
                if imp == prefix or imp.startswith(prefix + "."):
                    violations.append(f"{fname}: {imp}")
    assert violations == [], f"Forbidden imports: {violations}"


def test_service_does_not_import_synapse(rpe_imports: dict[str, set[str]]) -> None:
    """service.py must NOT touch app.synapse directly — only via mutator."""
    imports = rpe_imports.get("service.py", set())
    for imp in imports:
        assert not imp.startswith("app.synapse"), f"service.py imports {imp}"


def test_service_does_not_import_lock_manager(rpe_imports: dict[str, set[str]]) -> None:
    """Service uses internal asyncio.Lock registry, not LockManager."""
    imports = rpe_imports.get("service.py", set())
    for imp in imports:
        assert "lock_manager" not in imp, f"service.py imports {imp}"


def test_no_routes_or_main_imports(rpe_imports: dict[str, set[str]]) -> None:
    for fname, imports in rpe_imports.items():
        for imp in imports:
            assert imp != "app.main", f"{fname} imports app.main"
            assert "app.api.routes" not in imp, f"{fname} imports routes"
            # Precise check: forbid app.execution.swarm (the runtime module)
            # but allow app.execution.swarm_models (pure data — used by pipeline.py).
            assert not (
                imp == "app.execution.swarm"
                or imp.startswith("app.execution.swarm.")
            ), f"{fname} imports swarm runtime: {imp}"


def test_no_basal_ganglia_or_cr(rpe_imports: dict[str, set[str]]) -> None:
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


def test_no_llm_or_embedder_imports(rpe_imports: dict[str, set[str]]) -> None:
    llm_markers = ("anthropic", "openai", "sentence_transformers", "transformers")
    for fname, imports in rpe_imports.items():
        for imp in imports:
            for marker in llm_markers:
                assert not imp.startswith(marker), f"{fname} imports {imp}"


def test_mutators_synapse_import_allowed() -> None:
    """STEP 3.1 explicitly permits app.synapse adapter usage in mutators.py."""
    # We don't import app.synapse.store directly in mutators.py at module level;
    # SynapseStoreAdapter takes any object duck-typed to get_state/update_state.
    # This test asserts that no forbidden synapse modules are imported.
    imports = _collect_imports(RPE_ROOT / "mutators.py")
    forbidden_synapse = (
        "app.synapse.observer",
        "app.synapse.policies",
        "app.synapse.snapshot",
    )
    for imp in imports:
        for prefix in forbidden_synapse:
            assert not imp.startswith(prefix), f"mutators.py imports {imp}"


def test_models_does_not_import_synapse_or_service() -> None:
    """models.py must remain a pure data layer."""
    imports = _collect_imports(RPE_ROOT / "models.py")
    for imp in imports:
        assert not imp.startswith("app.synapse"), f"models.py imports {imp}"
        assert not imp.startswith("app.rpe.service"), imp
        assert not imp.startswith("app.rpe.mutators"), imp


def test_calculators_does_not_import_synapse_or_service() -> None:
    imports = _collect_imports(RPE_ROOT / "calculators.py")
    for imp in imports:
        assert not imp.startswith("app.synapse"), imp
        assert not imp.startswith("app.rpe.service"), imp
        assert not imp.startswith("app.rpe.mutators"), imp
