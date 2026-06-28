"""B8 — CrossroadReasoner AST isolation: it is a leaf orchestrator.

It reads the 35-cell weight (difficulty_store protocol) and runs the explore via
an INJECTED runner, so it must NOT import app.rpe.pipeline / app.rpe.service /
app.execution / app.api.routes / app.main, nor any LLM/embedder library (it
reaches an LLM only through the injected swarm runner).
"""
from __future__ import annotations

import ast
from pathlib import Path

CROSSROAD = Path(__file__).resolve().parents[2] / "app" / "routing" / "crossroad.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module)
    return out


_FORBIDDEN_PREFIXES = (
    "app.rpe.pipeline",
    "app.rpe.service",
    "app.rpe.mutators",
    "app.rpe.dopamine",
    "app.execution",
    "app.api.routes",
    "app.main",
    "app.maintenance",
)

_FORBIDDEN_LIBS = (
    "openai",
    "anthropic",
    "litellm",
    "transformers",
    "sentence_transformers",
    "chromadb",
)


def test_crossroad_no_forbidden_app_imports():
    violations = []
    for imp in _imports(CROSSROAD):
        for prefix in _FORBIDDEN_PREFIXES:
            if imp == prefix or imp.startswith(prefix + "."):
                violations.append(imp)
    assert violations == [], f"crossroad.py must not import: {violations}"


def test_crossroad_no_llm_or_embedder_imports():
    for imp in _imports(CROSSROAD):
        for lib in _FORBIDDEN_LIBS:
            assert not (imp == lib or imp.startswith(lib + ".")), (
                f"crossroad.py must not import {imp!r} (reaches LLM only via runner)"
            )


def test_crossroad_only_allowed_app_imports():
    # difficulty_store (read protocol), skip_router (RouteDecision), schemas
    # context (TaskContext), core.logging — the injected runner keeps pipeline/
    # swarm out.
    allowed = {
        "app.api.schemas.context",
        "app.core.logging",
        "app.rpe.difficulty_store",
        "app.routing.skip_router",
    }
    violations = [
        imp
        for imp in _imports(CROSSROAD)
        if imp.startswith("app.") and imp not in allowed
    ]
    assert violations == [], (
        f"crossroad.py may only import {sorted(allowed)} from app.*; got {violations}"
    )
