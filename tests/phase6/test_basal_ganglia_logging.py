"""Phase 6 STEP 5.1 — BasalGanglia logging tests."""
from __future__ import annotations

import asyncio

import pytest

from app.basal_ganglia.advisor import BasalGangliaAdvisor
from app.basal_ganglia.models import ActionSelectionContext
from app.basal_ganglia.policies import ActionSelectionPolicy
from app.core.logging import get_spinal_logger


def _ctx(trace_id: str) -> ActionSelectionContext:
    return ActionSelectionContext(
        trace_id=trace_id, session_id="s", category="coding", difficulty=2,
        pfc_active=False, pfc_cue_type=None, pfc_confidence=0.5,
        pfc_intent_category=None, lc_ne_level=0.2, lc_intent_label=None,
    )


# ---------------------------------------------------------------------------
# bg.evaluated payload
# ---------------------------------------------------------------------------


def test_bg_evaluated_module_name():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "tr-mod-name"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    bg_events = [e for e in events if e.event_type == "bg.evaluated"]
    assert len(bg_events) == 1
    assert bg_events[0].module_name == "basal_ganglia"


def test_bg_evaluated_payload_keys():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "tr-payload-keys"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    bg_events = [e for e in events if e.event_type == "bg.evaluated"]
    payload = bg_events[0].payload
    expected_keys = {
        "trace_id", "candidates_count", "selected_id", "selected_type",
        "confidence", "reason", "category", "applied",
    }
    assert expected_keys.issubset(set(payload.keys()))


def test_bg_evaluated_trace_id_in_payload():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "tr-payload-tid"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    bg = [e for e in events if e.event_type == "bg.evaluated"]
    assert bg[0].trace_id == trace_id
    assert bg[0].payload["trace_id"] == trace_id


def test_bg_evaluated_applied_false():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(logger=logger)
    trace_id = "tr-applied-false"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    bg = [e for e in events if e.event_type == "bg.evaluated"]
    assert bg[0].payload["applied"] is False


# ---------------------------------------------------------------------------
# bg.error payload
# ---------------------------------------------------------------------------


class _ErrorPolicy(ActionSelectionPolicy):
    def select(self, context, candidates):
        raise ValueError("forced for test")


def test_bg_error_module_name():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(policy=_ErrorPolicy(), logger=logger)
    trace_id = "tr-err-mod"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    err = [e for e in events if e.event_type == "bg.error"]
    assert len(err) == 1
    assert err[0].module_name == "basal_ganglia"


def test_bg_error_payload_keys():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(policy=_ErrorPolicy(), logger=logger)
    trace_id = "tr-err-keys"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    err = [e for e in events if e.event_type == "bg.error"]
    payload = err[0].payload
    expected = {"trace_id", "error_type", "error", "applied"}
    assert expected.issubset(set(payload.keys()))


def test_bg_error_payload_values():
    logger = get_spinal_logger()
    advisor = BasalGangliaAdvisor(policy=_ErrorPolicy(), logger=logger)
    trace_id = "tr-err-vals"
    asyncio.run(advisor.evaluate(_ctx(trace_id)))
    events = logger.get_trace(trace_id)
    err = [e for e in events if e.event_type == "bg.error"]
    payload = err[0].payload
    assert payload["error_type"] == "ValueError"
    assert payload["error"] == "forced for test"
    assert payload["applied"] is False
    assert payload["trace_id"] == trace_id
