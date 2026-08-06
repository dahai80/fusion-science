from __future__ import annotations

import time

import pytest

from fusion_science.session.manager import SessionManager
from fusion_science.session.models import Artifact, ResearchContext, ResearchSession
from fusion_science.session.store import MemorySessionStore, SQLiteSessionStore
from fusion_science.utils.events import EVENT_SESSION_CREATED, EVENT_SESSION_UPDATED, EventBus, reset_event_bus


@pytest.fixture
def bus():
    reset_event_bus()
    bus = EventBus()
    yield bus
    reset_event_bus()


def _make_session(**kw):
    now = time.time()
    defaults = dict(
        id="test-1",
        title="Test Session",
        created_at=now,
        updated_at=now,
        messages=[],
        context=ResearchContext(),
        artifacts=[],
        trace_ids=[],
    )
    defaults.update(kw)
    return ResearchSession(**defaults)


class TestModels:
    def test_artifact_to_dict(self):
        a = Artifact(id="a1", type="chart", name="fig1", content="data", metadata={}, created_at=1.0)
        d = a.to_dict()
        assert d["id"] == "a1"
        assert d["type"] == "chart"

    def test_research_context_default(self):
        ctx = ResearchContext()
        assert ctx.papers == []
        assert ctx.datasets == []

    def test_session_to_dict_roundtrip(self):
        s = _make_session(messages=[{"role": "user", "content": "hi"}])
        d = s.to_dict()
        s2 = ResearchSession.from_dict(d)
        assert s2.id == s.id
        assert s2.messages[0]["content"] == "hi"

    def test_session_from_dict_missing_fields(self):
        d = {"id": "x", "title": "y"}
        s = ResearchSession.from_dict(d)
        assert s.id == "x"
        assert s.messages == []
        assert s.artifacts == []


class TestMemorySessionStore:
    def test_save_and_load(self):
        store = MemorySessionStore()
        s = _make_session()
        store.save(s)
        loaded = store.load("test-1")
        assert loaded is not None
        assert loaded.id == "test-1"

    def test_load_missing(self):
        store = MemorySessionStore()
        assert store.load("nope") is None

    def test_delete(self):
        store = MemorySessionStore()
        store.save(_make_session())
        assert store.delete("test-1") is True
        assert store.load("test-1") is None

    def test_delete_missing(self):
        store = MemorySessionStore()
        assert store.delete("nope") is False

    def test_list_all(self):
        store = MemorySessionStore()
        store.save(_make_session(id="a"))
        store.save(_make_session(id="b"))
        assert len(store.list_all()) == 2

    def test_eviction(self):
        store = MemorySessionStore(max_sessions=3)
        for i in range(5):
            store.save(_make_session(id=f"s-{i}"))
        assert len(store.list_all()) == 3


class TestSQLiteSessionStore:
    def test_save_and_load(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = SQLiteSessionStore(db_path=db)
        s = _make_session()
        store.save(s)
        loaded = store.load("test-1")
        assert loaded is not None
        assert loaded.title == "Test Session"

    def test_load_missing(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        assert store.load("nope") is None

    def test_delete(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        store.save(_make_session())
        assert store.delete("test-1") is True
        assert store.load("test-1") is None

    def test_list_all(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        store.save(_make_session(id="a", title="A"))
        store.save(_make_session(id="b", title="B"))
        sessions = store.list_all()
        assert len(sessions) == 2

    def test_upsert(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        store.save(_make_session(title="V1"))
        store.save(_make_session(title="V2"))
        loaded = store.load("test-1")
        assert loaded.title == "V2"


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_create_session(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session(title="Hello")
        assert session.title == "Hello"
        assert session.id

    @pytest.mark.asyncio
    async def test_create_session_default_title(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        assert session.title.startswith("Research ")

    @pytest.mark.asyncio
    async def test_get_session(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        loaded = mgr.get_session(session.id)
        assert loaded is not None
        assert loaded.id == session.id

    @pytest.mark.asyncio
    async def test_list_sessions(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        await mgr.create_session()
        await mgr.create_session()
        assert len(mgr.list_sessions()) == 2

    @pytest.mark.asyncio
    async def test_delete_session(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        assert await mgr.delete_session(session.id) is True
        assert mgr.get_session(session.id) is None

    @pytest.mark.asyncio
    async def test_add_message(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        updated = await mgr.add_message(session.id, "user", "hello")
        assert updated is not None
        assert len(updated.messages) == 1
        assert updated.messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_add_message_missing(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        result = await mgr.add_message("nope", "user", "hi")
        assert result is None

    @pytest.mark.asyncio
    async def test_add_artifact(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        art = Artifact(id="a1", type="chart", name="fig1", content="data", metadata={}, created_at=time.time())
        updated = await mgr.add_artifact(session.id, art)
        assert updated is not None
        assert len(updated.artifacts) == 1

    @pytest.mark.asyncio
    async def test_get_messages(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        await mgr.add_message(session.id, "user", "hi")
        await mgr.add_message(session.id, "assistant", "hello")
        msgs = mgr.get_messages(session.id)
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_get_messages_missing(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        assert mgr.get_messages("nope") == []

    @pytest.mark.asyncio
    async def test_update_title(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session(title="Old")
        updated = await mgr.update_title(session.id, "New")
        assert updated.title == "New"

    @pytest.mark.asyncio
    async def test_event_emitted_on_create(self, bus):
        events = []

        async def capture(e):
            events.append(e)

        bus.on(EVENT_SESSION_CREATED, capture)
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        await mgr.create_session()
        assert len(events) == 1
        assert events[0].type == EVENT_SESSION_CREATED

    @pytest.mark.asyncio
    async def test_event_emitted_on_update(self, bus):
        events = []

        async def capture(e):
            events.append(e)

        bus.on(EVENT_SESSION_UPDATED, capture)
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        await mgr.add_message(session.id, "user", "hi")
        assert len(events) == 1
        assert events[0].data["action"] == "add_message"
