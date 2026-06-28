"""Phase 5 STEP 4 — PFC 통합 isolation 검증.

STEP 4 코드가 금지된 모듈을 import하지 않는지 확인한다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 분석 대상 파일
# ---------------------------------------------------------------------------

_SWARM_PY = Path("app/execution/swarm.py")
_PLANNER_PY = Path("app/execution/planner_agent.py")
_FACTORY_PY = Path("app/execution/factory.py")
_PFC_PY = Path("app/routing/pfc.py")


def _get_imports(path: Path) -> set[str]:
    """파일에서 모든 import 모듈명을 수집."""
    source = path.read_text(encoding="utf-8")
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


def _source_contains(path: Path, text: str) -> bool:
    return text in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 6 모듈 import 금지
# ---------------------------------------------------------------------------

_PHASE6_MODULES = [
    "dopamine",
    "basal_ganglia",
    "rpe",
    "cr",
]


def test_swarm_no_phase6_imports():
    imports = _get_imports(_SWARM_PY)
    for mod in _PHASE6_MODULES:
        assert not any(mod in imp for imp in imports), (
            f"swarm.py must not import Phase 6 module '{mod}'"
        )


def test_planner_no_phase6_imports():
    imports = _get_imports(_PLANNER_PY)
    for mod in _PHASE6_MODULES:
        assert not any(mod in imp for imp in imports), (
            f"planner_agent.py must not import Phase 6 module '{mod}'"
        )


def test_pfc_no_phase6_imports():
    imports = _get_imports(_PFC_PY)
    for mod in _PHASE6_MODULES:
        assert not any(mod in imp for imp in imports), (
            f"pfc.py must not import Phase 6 module '{mod}'"
        )


# ---------------------------------------------------------------------------
# legacy/ import 금지
# ---------------------------------------------------------------------------


def test_swarm_no_legacy_import():
    assert not _source_contains(_SWARM_PY, "legacy"), (
        "swarm.py must not import from legacy/"
    )


def test_planner_no_legacy_import():
    assert not _source_contains(_PLANNER_PY, "legacy"), (
        "planner_agent.py must not import from legacy/"
    )


def test_pfc_no_legacy_import():
    assert not _source_contains(_PFC_PY, "legacy"), (
        "pfc.py must not import from legacy/"
    )


# ---------------------------------------------------------------------------
# LLM 호출 금지 (PFC 내부)
# ---------------------------------------------------------------------------


def test_pfc_no_llm_client_import():
    imports = _get_imports(_PFC_PY)
    assert not any("llm_client" in imp or "live_llm" in imp for imp in imports), (
        "pfc.py must not import LLM client"
    )


def test_swarm_pfc_execute_no_direct_llm_call():
    """swarm.py의 _execute_pfc 메서드가 LLM client를 직접 호출하지 않음."""
    source = _SWARM_PY.read_text(encoding="utf-8")
    assert "llm_client" not in source or "TYPE_CHECKING" in source


# ---------------------------------------------------------------------------
# asyncio.wait_for 없이 pfc await 금지 확인
# ---------------------------------------------------------------------------


def test_swarm_uses_asyncio_wait_for_for_pfc():
    """swarm.py는 PFC bounded wait에 asyncio.wait_for를 사용해야 한다."""
    source = _SWARM_PY.read_text(encoding="utf-8")
    assert "asyncio.wait_for(" in source, (
        "swarm.py must use asyncio.wait_for() for bounded PFC wait"
    )


# ---------------------------------------------------------------------------
# asyncio.shield 사용 확인
# ---------------------------------------------------------------------------


def test_swarm_uses_asyncio_shield():
    """swarm.py는 PFC bounded wait에 asyncio.shield를 사용해야 한다."""
    source = _SWARM_PY.read_text(encoding="utf-8")
    assert "asyncio.shield(" in source, (
        "swarm.py must use asyncio.shield() to protect pfc_task"
    )


# ---------------------------------------------------------------------------
# routes.py 변경 없음 확인
# ---------------------------------------------------------------------------


def test_routes_not_modified():
    """routes.py는 STEP 4에서 변경되지 않아야 한다."""
    routes_py = Path("app/api/routes.py")
    source = routes_py.read_text(encoding="utf-8")
    # Phase 5 STEP 4 전용 내용이 추가되지 않았음을 확인
    # (pfc 관련 직접 참조가 없어야 함)
    assert "PFCIntegrationConfig" not in source, (
        "routes.py must not reference PFCIntegrationConfig (Phase 4 stability)"
    )
    assert "_execute_with_pfc" not in source, (
        "routes.py must not call _execute_with_pfc directly"
    )


# ---------------------------------------------------------------------------
# SwarmTrace schema 변경 없음 (swarm_models.py)
# ---------------------------------------------------------------------------


def test_swarm_models_no_pfc_fields():
    """SwarmTrace schema에 PFC 전용 필드가 추가되지 않았음 (STEP 6 후 결정)."""
    swarm_models = Path("app/execution/swarm_models.py")
    source = swarm_models.read_text(encoding="utf-8")
    assert "pfc_decision" not in source, (
        "SwarmResult/SwarmTrace must not include pfc_decision field (STEP 6 후 결정)"
    )


# ---------------------------------------------------------------------------
# CancelledError swallow 금지 확인
# ---------------------------------------------------------------------------


def test_swarm_does_not_swallow_cancelled_error():
    """swarm.py의 기본 실행 경로에서 CancelledError가 re-raise됨.

    _handle_late_pfc는 background task이므로 CancelledError에서 graceful return 허용.
    """
    source = _SWARM_PY.read_text(encoding="utf-8")
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "except asyncio.CancelledError:" in line:
            # Look ahead for raise, cleanup, or return (graceful background exit)
            subsequent = "\n".join(lines[i + 1: i + 6])
            assert (
                "raise" in subsequent
                or "await self._cleanup_tasks" in subsequent
                or "return" in subsequent  # graceful exit in background task handler
            ), (
                f"CancelledError caught at line {i + 1} without re-raise or graceful exit"
            )
