"""Phase 6 STEP 5.1 — BasalGanglia AST isolation tests."""
from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
BG_DIR = APP_ROOT / "basal_ganglia"


def _get_imports(filepath: Path) -> set[str]:
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


def _bg_files() -> list[Path]:
    return sorted(BG_DIR.glob("*.py"))


# ---------------------------------------------------------------------------
# BasalGanglia files exist
# ---------------------------------------------------------------------------


def test_basal_ganglia_dir_exists():
    assert BG_DIR.is_dir(), f"{BG_DIR} not found"


def test_basal_ganglia_files_present():
    files = {p.name for p in _bg_files()}
    assert files >= {"__init__.py", "models.py", "policies.py", "advisor.py"}


# ---------------------------------------------------------------------------
# Forbidden imports from BasalGanglia
# ---------------------------------------------------------------------------


_FORBIDDEN_BG_PREFIXES = (
    "app.execution",
    "app.api",
    "app.main",
    "app.routing.pfc",
    "app.routing.lc",
    "app.routing.skip_router",
    "app.routing.tier1_5",
    "app.routing.semantic_evaluator",
    "app.routing.centroid_store",
    "app.routing.continuation_detector",
    "app.routing.cue_classifier",
    "app.routing.neuromodulators",
    "app.rpe",
    "app.memory",
    "app.synapse",
    "app.maintenance",
    "app.ingress",
)


def test_basal_ganglia_no_forbidden_imports():
    violations: list[str] = []
    for f in _bg_files():
        imports = _get_imports(f)
        for imp in imports:
            for prefix in _FORBIDDEN_BG_PREFIXES:
                if imp == prefix or imp.startswith(prefix + "."):
                    violations.append(f"{f.name}: {imp}")
    assert violations == [], f"BasalGanglia must not import: {violations}"


def test_basal_ganglia_no_llm_imports():
    forbidden_llm = ("openai", "anthropic", "litellm", "transformers")
    for f in _bg_files():
        imports = _get_imports(f)
        for imp in imports:
            for prefix in forbidden_llm:
                assert not (imp == prefix or imp.startswith(prefix + ".")), (
                    f"{f.name}: must not import LLM module {imp!r}"
                )


def test_basal_ganglia_no_embedder_imports():
    forbidden = ("sentence_transformers", "chromadb")
    for f in _bg_files():
        imports = _get_imports(f)
        for imp in imports:
            for prefix in forbidden:
                assert not (imp == prefix or imp.startswith(prefix + ".")), (
                    f"{f.name}: must not import embedder module {imp!r}"
                )


def test_basal_ganglia_no_legacy_imports():
    """No legacy v0.4/v0.5 patterns."""
    legacy = ("app.legacy", "app.v0_4", "app.v0_5")
    for f in _bg_files():
        imports = _get_imports(f)
        for imp in imports:
            for prefix in legacy:
                assert not (imp == prefix or imp.startswith(prefix + ".")), (
                    f"{f.name}: legacy import {imp!r}"
                )


# ---------------------------------------------------------------------------
# Only safe app imports from BasalGanglia
# ---------------------------------------------------------------------------


def test_basal_ganglia_only_safe_app_imports():
    allowed = {
        "app.core.logging",  # SpinalLogger
        "app.basal_ganglia.models",
        "app.basal_ganglia.policies",
        "app.basal_ganglia.advisor",
    }
    violations: list[str] = []
    for f in _bg_files():
        imports = _get_imports(f)
        for imp in imports:
            if imp.startswith("app.") and imp not in allowed:
                violations.append(f"{f.name}: {imp}")
    assert violations == [], (
        f"BasalGanglia may only import {sorted(allowed)}; got: {violations}"
    )


# ---------------------------------------------------------------------------
# Other modules must NOT import BasalGanglia (STEP 5.1 isolation)
# ---------------------------------------------------------------------------


def _imports_basal_ganglia(filepath: Path) -> bool:
    imports = _get_imports(filepath)
    return any(
        imp == "app.basal_ganglia" or imp.startswith("app.basal_ganglia.")
        for imp in imports
    )


def test_routing_pfc_does_not_import_bg():
    f = APP_ROOT / "routing" / "pfc.py"
    assert not _imports_basal_ganglia(f), (
        "app/routing/pfc.py must not import app.basal_ganglia in STEP 5.1"
    )


def test_routing_lc_does_not_import_bg():
    f = APP_ROOT / "routing" / "lc.py"
    assert not _imports_basal_ganglia(f), (
        "app/routing/lc.py must not import app.basal_ganglia in STEP 5.1"
    )


def test_execution_swarm_does_not_import_bg():
    f = APP_ROOT / "execution" / "swarm.py"
    assert not _imports_basal_ganglia(f), (
        "app/execution/swarm.py must not import app.basal_ganglia in STEP 5.1"
    )


def test_api_routes_imports_bg_b7():
    """B7 — routes now wires the BG advisor (one-way: routes → BG).

    STEP 5.1 forbade this import; B7 reverses it for production wiring. The
    direction stays one-way — BG never imports app.api (enforced by
    test_basal_ganglia_no_forbidden_imports above).
    """
    f = APP_ROOT / "api" / "routes.py"
    assert _imports_basal_ganglia(f), (
        "B7: app/api/routes.py must import app.basal_ganglia (production wiring)"
    )


def test_main_imports_bg_b7():
    """B7 — main injects the BG advisor onto app.state (one-way: main → BG)."""
    f = APP_ROOT / "main.py"
    assert _imports_basal_ganglia(f), (
        "B7: app/main.py must import app.basal_ganglia (DI wiring)"
    )


def test_rpe_pipeline_does_not_import_bg():
    f = APP_ROOT / "rpe" / "pipeline.py"
    assert not _imports_basal_ganglia(f), (
        "app/rpe/pipeline.py must not import app.basal_ganglia in STEP 5.1"
    )
