import pytest

from app.core.logging import SpinalLogger, get_spinal_logger


@pytest.mark.asyncio
async def test_new_trace_returns_unique_ids():
    logger = get_spinal_logger()
    a = await logger.new_trace()
    b = await logger.new_trace()
    assert a != b
    assert len(a) > 0 and len(b) > 0


@pytest.mark.asyncio
async def test_log_event_is_retrievable():
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    await logger.log_event(
        trace_id=trace_id,
        module_name="test.module",
        event_type="unit.test",
        payload={"k": "v"},
    )
    events = logger.get_trace(trace_id)
    assert len(events) == 1
    assert events[0].module_name == "test.module"
    assert events[0].event_type == "unit.test"
    assert events[0].payload == {"k": "v"}


@pytest.mark.asyncio
async def test_event_order_preserved():
    logger = get_spinal_logger()
    trace_id = await logger.new_trace()
    for i in range(5):
        await logger.log_event(
            trace_id=trace_id,
            module_name="test.order",
            event_type=f"step.{i}",
            payload={"i": i},
        )
    events = logger.get_trace(trace_id)
    assert [e.event_type for e in events] == [f"step.{i}" for i in range(5)]
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


def test_logger_is_singleton():
    assert SpinalLogger() is SpinalLogger()
    assert get_spinal_logger() is SpinalLogger()
