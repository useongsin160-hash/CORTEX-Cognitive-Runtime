"""Phase 4 STEP 3.2 — Swarm scope-control checks."""
from __future__ import annotations

import inspect
import json

import app.execution.swarm as swarm_module
from app.execution.context_models import ContextAgentResult
from app.execution.plan_models import FinalPlan, GeneratorResult
from app.execution.swarm_models import SwarmResult


def _import_lines(src: str) -> str:
    return "\n".join(
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    )


def test_swarm_does_not_import_routes():
    imports = _import_lines(inspect.getsource(swarm_module))
    assert "routes" not in imports


def test_swarm_plc_is_type_checking_only():
    """Phase 4 STEP 4: PLC is injected via DI and may only appear under
    TYPE_CHECKING (no runtime import).  LockManager must not be imported
    by swarm.py directly — it lives in core and is accessed via PLC.
    """
    source = inspect.getsource(swarm_module)
    # lock_manager must not be imported at all (PLC encapsulates it).
    imports = _import_lines(source)
    assert "lock_manager" not in imports

    # PLC import must be guarded by TYPE_CHECKING, not a top-level import.
    import re
    plc_import_lines = [
        line for line in source.splitlines()
        if re.search(r'from\s+app\.maintenance\.plc\s+import', line)
        or re.search(r'import\s+app\.maintenance\.plc', line)
    ]
    for line in plc_import_lines:
        # Each PLC import line must be indented (inside an if TYPE_CHECKING block).
        assert line.startswith("    "), (
            f"PLC import must be inside TYPE_CHECKING block, found: {line!r}"
        )


def test_swarm_does_not_import_vendor_llm_sdk():
    imports = _import_lines(inspect.getsource(swarm_module))
    for sdk in ("import anthropic", "import openai", "from anthropic", "from openai"):
        assert sdk not in imports


def test_swarm_uses_asyncio_gather():
    """gather는 Swarm의 본업 — 사용되어야 한다."""
    assert "asyncio.gather" in inspect.getsource(swarm_module)


def test_swarm_result_carries_no_runtime_objects():
    """SwarmResult는 순수 Pydantic — model_dump_json이 깨지면 안 된다."""
    result = SwarmResult(
        context_result=ContextAgentResult(),
        final_plan=FinalPlan(intent="answer", prompt_for_generator="q"),
        generator_result=GeneratorResult(
            text="o", tier_used="STANDARD", model_name="m",
            prompt_tokens=1, completion_tokens=1, finish_reason="stop",
            latency_ms=1.0, ne_applied=False, plan_intent="answer",
        ),
        context_status="ok", planner_status="ok", generator_status="ok",
        total_elapsed_ms=1.0,
    )
    json.loads(result.model_dump_json())  # must not raise
