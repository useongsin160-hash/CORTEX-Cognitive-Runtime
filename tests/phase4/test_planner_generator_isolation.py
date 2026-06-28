"""Phase 4 STEP 3.1 — 원칙 3·4: routes/swarm 미통합, gather 미사용.

Isolation은 import 문 기준으로 검사한다 — docstring/주석에서 'swarm',
'routes'를 언급하는 것은 허용 (실제 import / 호출만 금지).
"""
from __future__ import annotations

import inspect

import app.execution.generator_agent as generator_module
import app.execution.planner_agent as planner_module


def _import_lines(src: str) -> str:
    """Return only the import statements from a module source string."""
    return "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    )


def test_planner_does_not_import_routes():
    imports = _import_lines(inspect.getsource(planner_module))
    assert "routes" not in imports


def test_generator_does_not_import_routes():
    imports = _import_lines(inspect.getsource(generator_module))
    assert "routes" not in imports


def test_planner_does_not_import_swarm():
    imports = _import_lines(inspect.getsource(planner_module))
    assert "swarm" not in imports


def test_generator_does_not_import_swarm():
    imports = _import_lines(inspect.getsource(generator_module))
    assert "swarm" not in imports


def test_planner_does_not_use_asyncio_gather():
    assert "gather(" not in inspect.getsource(planner_module)


def test_generator_does_not_use_asyncio_gather():
    assert "gather(" not in inspect.getsource(generator_module)
