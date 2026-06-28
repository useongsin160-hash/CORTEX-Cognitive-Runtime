"""Phase 4 STEP 3.3a/3.3b — routes.py가 외부 모듈 직접 의존하지 않는다.

STEP 3.3a에서는 routes가 AsyncSwarm을 아예 import하지 않음을 검증했다.
STEP 3.3b에서는 통합이 들어왔으므로 (`from app.api.schemas.response
import SwarmTrace`, `state.async_swarm.execute()`) 범위가 좁아진다.
지금 검증하는 것은:
  - vendor LLM SDK 직접 import 금지 (mock/live 분리는 factory가 담당).
  - AsyncSwarm 클래스 자체를 routes가 import하지 않음 — state 핸들만 사용.
  - factory 모듈 직접 import 금지 — main.py lifespan에서만 build.
"""
from __future__ import annotations

import inspect

import app.api.routes as routes_module


def _import_lines(src: str) -> str:
    return "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    )


def test_routes_does_not_import_async_swarm_class():
    """state.async_swarm 핸들로 호출하되, 클래스 자체는 import하지 않는다."""
    imports = _import_lines(inspect.getsource(routes_module))
    assert "AsyncSwarm" not in imports
    assert "from app.execution.swarm" not in imports


def test_routes_does_not_construct_swarm_directly():
    """AsyncSwarm 인스턴스화는 factory + main lifespan의 책임."""
    src = inspect.getsource(routes_module)
    assert "AsyncSwarm(" not in src


def test_routes_does_not_import_factory_module():
    """factory.py는 main.py lifespan에서만 import — routes는 state 핸들만 사용."""
    imports = _import_lines(inspect.getsource(routes_module))
    assert "app.execution.factory" not in imports


def test_routes_does_not_import_vendor_llm_sdk():
    imports = _import_lines(inspect.getsource(routes_module))
    for sdk in ("import anthropic", "import openai",
                "from anthropic", "from openai"):
        assert sdk not in imports
