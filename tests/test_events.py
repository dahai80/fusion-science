from __future__ import annotations

import pytest

from fusion_science.utils.events import (
    EVENT_CODE_EXECUTION,
    EVENT_DB_QUERY,
    EVENT_ERROR,
    EVENT_LLM_CALL,
    Event,
    EventBus,
    get_event_bus,
    reset_event_bus,
)


class TestEvent:
    def test_event_creation(self):
        e = Event(type=EVENT_DB_QUERY, data={"db": "pubmed"}, source="test")
        assert e.type == EVENT_DB_QUERY
        assert e.data["db"] == "pubmed"
        assert e.source == "test"


class TestEventBus:
    def setup_method(self):
        reset_event_bus()

    def test_empty_bus(self):
        bus = EventBus()
        assert bus.handler_count("any") == 0
        assert bus.get_history() == []

    @pytest.mark.asyncio
    async def test_on_and_emit(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.on(EVENT_DB_QUERY, handler)
        await bus.emit(EVENT_DB_QUERY, {"query": "cancer"}, source="test")
        assert len(received) == 1
        assert received[0].data["query"] == "cancer"
        assert received[0].timestamp > 0

    @pytest.mark.asyncio
    async def test_multiple_handlers(self):
        bus = EventBus()
        count_a = []
        count_b = []

        async def ha(e):
            count_a.append(1)

        async def hb(e):
            count_b.append(1)

        bus.on(EVENT_DB_QUERY, ha)
        bus.on(EVENT_DB_QUERY, hb)
        await bus.emit(EVENT_DB_QUERY, {}, source="test")
        assert len(count_a) == 1
        assert len(count_b) == 1

    @pytest.mark.asyncio
    async def test_off(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.on(EVENT_LLM_CALL, handler)
        bus.off(EVENT_LLM_CALL, handler)
        await bus.emit(EVENT_LLM_CALL, {}, source="test")
        assert len(received) == 0

    def test_handler_count(self):
        bus = EventBus()

        async def h1(e):
            pass

        async def h2(e):
            pass

        bus.on(EVENT_ERROR, h1)
        bus.on(EVENT_ERROR, h2)
        assert bus.handler_count(EVENT_ERROR) == 2

    @pytest.mark.asyncio
    async def test_get_history(self):
        bus = EventBus()
        bus.on(EVENT_DB_QUERY, lambda e: None)
        await bus.emit(EVENT_DB_QUERY, {"q": 1}, source="a")
        await bus.emit(EVENT_CODE_EXECUTION, {"code": "x=1"}, source="b")
        history = bus.get_history()
        assert len(history) == 2
        assert history[0].type == EVENT_DB_QUERY
        assert history[1].type == EVENT_CODE_EXECUTION

    @pytest.mark.asyncio
    async def test_history_max_size(self):
        bus = EventBus()

        async def noop(e):
            pass

        bus.on("test", noop)
        for i in range(1100):
            await bus.emit("test", {"i": i}, source="bench")
        history = bus.get_history(limit=2000)
        assert len(history) == 1000

    @pytest.mark.asyncio
    async def test_clear_history(self):
        bus = EventBus()
        bus.on("x", lambda e: None)
        await bus.emit("x", {}, source="test")
        assert len(bus.get_history()) == 1
        bus.clear_history()
        assert len(bus.get_history()) == 0

    @pytest.mark.asyncio
    async def test_emit_no_handler(self):
        bus = EventBus()
        await bus.emit("unknown_event", {}, source="test")
        assert len(bus.get_history()) == 1


class TestGlobalEventBus:
    def setup_method(self):
        reset_event_bus()

    def test_get_event_bus_singleton(self):
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_reset_creates_new(self):
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()
        assert bus1 is not bus2

    def test_event_type_constants(self):
        assert EVENT_DB_QUERY == "db_query"
        assert EVENT_CODE_EXECUTION == "code_execution"
        assert EVENT_LLM_CALL == "llm_call"
        assert EVENT_ERROR == "error"
