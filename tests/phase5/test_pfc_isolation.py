"""Phase 5 STEP 3 — app/routing/pfc.py 격리 검증."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PFC_PATH = Path(__file__).parent.parent.parent / "app" / "routing" / "pfc.py"

_PHASE6_KEYWORDS = {"dopamine", "basal_ganglia", "rpe", "cr"}
_LLM_KEYWORDS = {"openai", "anthropic", "langchain", "llm", "gpt", "claude_client"}
_EMBEDDER_KEYWORDS = {"sentence_transformers", "chromadb", "faiss", "annoy"}


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
# 금지 import 검증
# ---------------------------------------------------------------------------


def test_pfc_does_not_import_execution():
    imports = _get_imports(PFC_PATH)
    violations = {i for i in imports if i.startswith("app.execution")}
    assert not violations, f"pfc.py에 금지된 execution import: {violations}"


def test_pfc_does_not_import_maintenance():
    imports = _get_imports(PFC_PATH)
    violations = {i for i in imports if i.startswith("app.maintenance")}
    assert not violations, f"pfc.py에 금지된 maintenance import: {violations}"


def test_pfc_no_phase6_imports():
    imports = _get_imports(PFC_PATH)
    violations = {
        i for i in imports
        for kw in _PHASE6_KEYWORDS if kw in i.lower()
    }
    assert not violations, f"pfc.py에 Phase 6 금지 import: {violations}"


def test_pfc_no_llm_client_imports():
    imports = _get_imports(PFC_PATH)
    violations = {
        i for i in imports
        for kw in _LLM_KEYWORDS if kw in i.lower()
    }
    assert not violations, f"pfc.py에 LLM client import: {violations}"


def test_pfc_no_embedder_imports():
    imports = _get_imports(PFC_PATH)
    violations = {
        i for i in imports
        for kw in _EMBEDDER_KEYWORDS if kw in i.lower()
    }
    assert not violations, f"pfc.py에 embedder import: {violations}"


def test_pfc_no_legacy_imports():
    imports = _get_imports(PFC_PATH)
    legacy = {i for i in imports if "legacy" in i}
    assert not legacy, f"pfc.py에 legacy import: {legacy}"


# ---------------------------------------------------------------------------
# 소스 텍스트 레벨 검증
# ---------------------------------------------------------------------------


def test_pfc_source_no_asyncio_wait_for():
    """asyncio.wait_for는 호출자 책임 — pfc.py 내부 호출 금지."""
    source = PFC_PATH.read_text(encoding="utf-8")
    assert "asyncio.wait_for(" not in source, "pfc.py에 asyncio.wait_for() 호출 금지"


def test_pfc_source_no_goal_stack_direct_mutation():
    """PFC는 GoalStack을 직접 변경하지 않음 (add/update/remove 호출 금지)."""
    source = PFC_PATH.read_text(encoding="utf-8")
    # PFC may reference GoalStack types but must not call mutation methods
    assert "goal_stack.add(" not in source
    assert "goal_stack.update(" not in source
    assert "goal_stack.remove(" not in source


# ---------------------------------------------------------------------------
# pfc_stub.py 유지 검증 (LC 호환성)
# ---------------------------------------------------------------------------


def test_pfc_stub_still_exists():
    """pfc_stub.py는 LC 호환성을 위해 유지되어야 함."""
    stub_path = Path(__file__).parent.parent.parent / "app" / "routing" / "pfc_stub.py"
    assert stub_path.exists(), "pfc_stub.py가 삭제됨 — LC 호환성 파괴"


def test_pfc_stub_notify_pfc_present():
    source = (
        Path(__file__).parent.parent.parent / "app" / "routing" / "pfc_stub.py"
    ).read_text(encoding="utf-8")
    assert "notify_pfc" in source, "pfc_stub.py에서 notify_pfc 함수 누락"


# ---------------------------------------------------------------------------
# import 부작용 없음
# ---------------------------------------------------------------------------


def test_pfc_importable_without_side_effects():
    import sys
    for mod_name in list(sys.modules.keys()):
        if "app.routing.pfc" in mod_name:
            del sys.modules[mod_name]
    import app.routing.pfc  # noqa: F401
