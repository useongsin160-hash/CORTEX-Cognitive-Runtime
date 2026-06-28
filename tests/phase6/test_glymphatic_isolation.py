"""B9 — GlymphaticCleaner AST isolation: the cleaner is a leaf.

It must reach the learning/routing/persistence layers only through the injected
``AgeCleanableStore`` protocol — never by import. So app/maintenance/glymphatic.py
must NOT import app.rpe / app.routing / app.ingress / app.api / app.main, nor any
LLM or embedder library (no-LLM maintenance).
"""
from __future__ import annotations

import ast
from pathlib import Path

GLYMPHATIC = (
    Path(__file__).resolve().parents[2] / "app" / "maintenance" / "glymphatic.py"
)


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
    "app.rpe",
    "app.routing",
    "app.ingress",
    "app.api",
    "app.main",
    "app.synapse",
    "app.memory",
    "app.execution",
)

_FORBIDDEN_LIBS = (
    "openai",
    "anthropic",
    "litellm",
    "transformers",
    "sentence_transformers",
    "chromadb",
)


def test_glymphatic_no_forbidden_app_imports():
    violations = []
    for imp in _imports(GLYMPHATIC):
        for prefix in _FORBIDDEN_PREFIXES:
            if imp == prefix or imp.startswith(prefix + "."):
                violations.append(imp)
    assert violations == [], f"glymphatic.py must not import: {violations}"


def test_glymphatic_no_llm_or_embedder_imports():
    for imp in _imports(GLYMPHATIC):
        for lib in _FORBIDDEN_LIBS:
            assert not (imp == lib or imp.startswith(lib + ".")), (
                f"glymphatic.py must not import {imp!r} (no-LLM maintenance)"
            )


def test_glymphatic_only_safe_app_imports():
    allowed = {"app.core.logging"}
    violations = [
        imp
        for imp in _imports(GLYMPHATIC)
        if imp.startswith("app.") and imp not in allowed
    ]
    assert violations == [], (
        f"glymphatic.py may only import {sorted(allowed)} from app.*; got {violations}"
    )
