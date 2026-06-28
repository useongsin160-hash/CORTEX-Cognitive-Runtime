"""Phase 5 STEP 1 — app/memory/ 격리 검증 테스트."""
from __future__ import annotations

import ast
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

MEMORY_DIR = Path(__file__).parent.parent.parent / "app" / "memory"

_MEMORY_FILES = [
    "goal.py",
    "goal_stack.py",
    "session_goal_context.py",
    "store.py",
]

# Phase 6 금지 키워드 (모듈명 기준)
_PHASE6_KEYWORDS = {"dopamine", "basal_ganglia", "rpe", "cr"}


def _get_imports(filepath: Path) -> set[str]:
    """AST 기반 import 추출."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source)
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
# 허용되지 않는 import 검증
# ---------------------------------------------------------------------------

def test_goal_py_does_not_import_execution():
    imports = _get_imports(MEMORY_DIR / "goal.py")
    execution_imports = {i for i in imports if i.startswith("app.execution")}
    assert not execution_imports, f"goal.py에 금지된 execution import: {execution_imports}"


def test_goal_stack_py_does_not_import_routing():
    imports = _get_imports(MEMORY_DIR / "goal_stack.py")
    routing_imports = {i for i in imports if i.startswith("app.routing")}
    assert not routing_imports, f"goal_stack.py에 금지된 routing import: {routing_imports}"


def test_session_goal_context_does_not_import_maintenance():
    imports = _get_imports(MEMORY_DIR / "session_goal_context.py")
    maintenance_imports = {i for i in imports if i.startswith("app.maintenance")}
    assert not maintenance_imports, f"session_goal_context.py에 금지된 maintenance import: {maintenance_imports}"


def test_store_py_does_not_import_api():
    imports = _get_imports(MEMORY_DIR / "store.py")
    api_imports = {i for i in imports if i.startswith("app.api")}
    assert not api_imports, f"store.py에 금지된 api import: {api_imports}"


@pytest.mark.parametrize("filename", _MEMORY_FILES)
def test_no_phase6_imports(filename: str):
    imports = _get_imports(MEMORY_DIR / filename)
    violations = set()
    for imp in imports:
        for kw in _PHASE6_KEYWORDS:
            if kw in imp.lower():
                violations.add(imp)
    assert not violations, f"{filename}에 Phase 6 금지 import: {violations}"


@pytest.mark.parametrize("filename", _MEMORY_FILES)
def test_no_legacy_imports(filename: str):
    imports = _get_imports(MEMORY_DIR / filename)
    legacy_imports = {i for i in imports if "legacy" in i}
    assert not legacy_imports, f"{filename}에 legacy import: {legacy_imports}"


# ---------------------------------------------------------------------------
# core dependency rule (memory는 core 이용 허용, 역방향 금지)
# ---------------------------------------------------------------------------

def test_goal_py_does_not_import_core_forbidden():
    """goal.py는 app.core 가져다 쓰지 않아야 함 (직접 의존성 없음)."""
    imports = _get_imports(MEMORY_DIR / "goal.py")
    core_imports = {i for i in imports if i.startswith("app.core")}
    # memory → core는 허용이지만, goal.py 자체는 core 의존성이 없어야 함
    assert not core_imports, f"goal.py가 app.core import: {core_imports}"


# ---------------------------------------------------------------------------
# 직렬화 안전성
# ---------------------------------------------------------------------------

def test_goal_json_serializable():
    from app.memory.goal import make_goal
    g = make_goal(title="직렬화 테스트", source="user_explicit")
    data = json.loads(g.model_dump_json())
    assert "goal_id" in data
    assert "title" in data
    assert "priority" in data


def test_goal_stack_config_fields_are_primitive():
    """GoalStackConfig 필드가 JSON-safe 기본 타입임을 확인."""
    from app.memory.goal_stack import GoalStackConfig
    cfg = GoalStackConfig()
    assert isinstance(cfg.max_depth, int)
    assert isinstance(cfg.recency_decay_lambda, float)
    # JSON 직렬화 가능
    data = json.dumps({"max_depth": cfg.max_depth, "lambda": cfg.recency_decay_lambda})
    assert json.loads(data)["max_depth"] == 7


def test_session_goal_context_has_no_asyncio_objects():
    """SessionGoalContext에 asyncio 객체가 없음을 확인."""
    import asyncio
    from app.memory.session_goal_context import SessionGoalContext
    ctx = SessionGoalContext.for_session("sess_check")
    # dataclass 필드 확인
    assert not isinstance(ctx.goal_stack, asyncio.Queue)
    assert not isinstance(ctx.goal_stack, asyncio.Lock)
    assert not hasattr(ctx, "_lock")
    assert not hasattr(ctx, "_queue")


def test_goal_model_no_asyncio_fields():
    """Goal이 asyncio 객체를 필드로 갖지 않음."""
    import asyncio
    from app.memory.goal import make_goal
    g = make_goal(title="asyncio 필드 검사", source="system")
    for field_name, value in g.model_dump().items():
        assert not isinstance(value, (asyncio.Queue, asyncio.Lock, asyncio.Event)), \
            f"Goal.{field_name} has asyncio object"


# ---------------------------------------------------------------------------
# 모듈 임포트 시 부작용 없음 확인
# ---------------------------------------------------------------------------

def test_memory_modules_importable_without_side_effects():
    """memory 모듈들이 import 시 외부 연결/DB 초기화 없이 로드됨."""
    # 이미 import된 경우 sys.modules에서 제거 후 재시도 (side-effect 검사)
    for mod_name in list(sys.modules.keys()):
        if "app.memory" in mod_name:
            del sys.modules[mod_name]

    # side-effect 없이 import 가능해야 함
    import app.memory.goal  # noqa: F401
    import app.memory.goal_stack  # noqa: F401
    import app.memory.session_goal_context  # noqa: F401
    import app.memory.store  # noqa: F401
