"""Phase 4 STEP 2 — 원칙 10: Context Agent의 SynapseStore 직접 접근 금지."""
from __future__ import annotations

import inspect

from app.execution.context_agent import ContextAgent


def test_context_agent_constructor_has_no_synapse_store_param():
    """ContextAgent.__init__는 selector/searcher/gaba 3개만 받는다.

    SynapseStore 인스턴스를 직접 받으면 race condition 위험 — Context
    Agent는 TaskContext.synapse_snapshot dict만 참조해야 한다.
    """
    params = list(inspect.signature(ContextAgent.__init__).parameters)
    assert params == ["self", "selector", "searcher", "gaba"]


def test_context_agent_module_does_not_import_synapse_store():
    """app.synapse 모듈을 import하지 않는다 (주석 언급은 허용 — 실제
    import / 인스턴스화만 금지)."""
    import app.execution.context_agent as ca_module

    src = inspect.getsource(ca_module)
    assert "import app.synapse" not in src
    assert "from app.synapse" not in src
    # 인스턴스화 / 메서드 직접 호출 패턴이 없어야 한다.
    assert "SynapseStore(" not in src


def test_context_agent_reads_snapshot_dict_only():
    """retrieve()는 task_context.synapse_snapshot(dict)만 참조한다."""
    src = inspect.getsource(ContextAgent.retrieve)
    assert "synapse_snapshot" in src
    # SynapseStore.get_state 류의 직접 호출이 없어야 한다.
    assert "get_state" not in src
    assert ".snapshot(" not in src
