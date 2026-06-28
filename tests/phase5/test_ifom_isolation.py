"""Phase 5 STEP 2 — app/memory/ifom.py 격리 검증."""
from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

MEMORY_DIR = Path(__file__).parent.parent.parent / "app" / "memory"
IFOM_PATH = MEMORY_DIR / "ifom.py"
GOAL_STACK_PATH = MEMORY_DIR / "goal_stack.py"

_PHASE6_KEYWORDS = {"dopamine", "basal_ganglia", "rpe", "cr"}


def _get_imports(filepath: Path) -> set[str]:
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
# ifom.py 금지 import 검증
# ---------------------------------------------------------------------------

def test_ifom_does_not_import_execution():
    imports = _get_imports(IFOM_PATH)
    violations = {i for i in imports if i.startswith("app.execution")}
    assert not violations, f"ifom.py에 금지된 execution import: {violations}"


def test_ifom_does_not_import_routing():
    imports = _get_imports(IFOM_PATH)
    violations = {i for i in imports if i.startswith("app.routing")}
    assert not violations, f"ifom.py에 금지된 routing import: {violations}"


def test_ifom_does_not_import_maintenance():
    imports = _get_imports(IFOM_PATH)
    violations = {i for i in imports if i.startswith("app.maintenance")}
    assert not violations, f"ifom.py에 금지된 maintenance import: {violations}"


def test_ifom_does_not_import_api():
    imports = _get_imports(IFOM_PATH)
    violations = {i for i in imports if i.startswith("app.api")}
    assert not violations, f"ifom.py에 금지된 api import: {violations}"


def test_ifom_no_phase6_imports():
    imports = _get_imports(IFOM_PATH)
    violations = {
        i for i in imports
        for kw in _PHASE6_KEYWORDS if kw in i.lower()
    }
    assert not violations, f"ifom.py에 Phase 6 금지 import: {violations}"


def test_ifom_no_legacy_imports():
    imports = _get_imports(IFOM_PATH)
    legacy = {i for i in imports if "legacy" in i}
    assert not legacy, f"ifom.py에 legacy import: {legacy}"


def test_ifom_does_not_import_core():
    """ifom.py는 app.core 직접 의존성 없어야 함."""
    imports = _get_imports(IFOM_PATH)
    core_imports = {i for i in imports if i.startswith("app.core")}
    assert not core_imports, f"ifom.py에 app.core import: {core_imports}"


# ---------------------------------------------------------------------------
# goal_stack.py가 ifom.py를 import하지 않음 (역방향 의존성 금지)
# ---------------------------------------------------------------------------

def test_goal_stack_does_not_import_ifom():
    """goal_stack.py가 ifom을 import하면 자동 cleanup 부작용이 생길 수 있음."""
    imports = _get_imports(GOAL_STACK_PATH)
    ifom_imports = {i for i in imports if "ifom" in i.lower()}
    assert not ifom_imports, f"goal_stack.py가 ifom을 import함: {ifom_imports}"


def test_goal_stack_source_has_no_cleanup_code():
    """goal_stack.py 소스에 cleanup 관련 IFOM 호출이 없어야 함."""
    source = GOAL_STACK_PATH.read_text(encoding="utf-8")
    assert "from app.memory.ifom" not in source
    assert "import ifom" not in source


# ---------------------------------------------------------------------------
# 직렬화 안전성 (Config + Decision만)
# ---------------------------------------------------------------------------

def test_ifom_config_json_serializable():
    from app.memory.ifom import IFOMConfig
    cfg = IFOMConfig()
    data = dataclasses.asdict(cfg)
    json_str = json.dumps(data)
    loaded = json.loads(json_str)
    assert loaded["active_ttl_seconds"] == 3600.0
    assert loaded["low_priority_threshold"] == 0.3


def test_ifom_decision_json_serializable():
    from app.memory.ifom import IFOMDecision
    d = IFOMDecision(goal_id="g1", action="remove", reason="completed_ttl_exceeded", age_seconds=700.0)
    data = dataclasses.asdict(d)
    json_str = json.dumps(data)
    loaded = json.loads(json_str)
    assert loaded["action"] == "remove"


# ---------------------------------------------------------------------------
# ifom.py 임포트 시 부작용 없음
# ---------------------------------------------------------------------------

def test_ifom_importable_without_side_effects():
    import sys
    for mod_name in list(sys.modules.keys()):
        if "app.memory.ifom" in mod_name:
            del sys.modules[mod_name]
    import app.memory.ifom  # noqa: F401
