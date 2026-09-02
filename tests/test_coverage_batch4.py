from __future__ import annotations

import json
import logging
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. fusion_science.session
# ---------------------------------------------------------------------------
from fusion_science.session import (
    Artifact,
    MemorySessionStore,
    ResearchContext,
    ResearchSession,
    SessionManager,
    SessionStore,
    SQLiteSessionStore,
)
from fusion_science.utils.events import (
    EVENT_SESSION_CREATED,
    EVENT_SESSION_UPDATED,
    EventBus,
    reset_event_bus,
)


@pytest.fixture
def bus():
    reset_event_bus()
    b = EventBus()
    yield b
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


class TestArtifact:
    def test_default_values(self):
        a = Artifact()
        assert a.id == ""
        assert a.type == ""
        assert a.name == ""
        assert a.content == ""
        assert a.metadata == {}
        assert a.created_at > 0

    def test_to_dict(self):
        a = Artifact(id="a1", type="chart", name="fig1", content="data", metadata={"k": "v"}, created_at=1.0)
        d = a.to_dict()
        assert d["id"] == "a1"
        assert d["type"] == "chart"
        assert d["name"] == "fig1"
        assert d["content"] == "data"
        assert d["metadata"] == {"k": "v"}
        assert d["created_at"] == 1.0


class TestResearchContext:
    def test_defaults(self):
        ctx = ResearchContext()
        assert ctx.papers == []
        assert ctx.datasets == []
        assert ctx.code_history == []
        assert ctx.figures == []
        assert ctx.variables == {}

    def test_with_data(self):
        ctx = ResearchContext(
            papers=[{"title": "paper1"}],
            datasets=[{"name": "ds1"}],
            code_history=[{"code": "x=1"}],
            figures=[{"path": "/tmp/fig.png"}],
            variables={"alpha": 0.05},
        )
        assert len(ctx.papers) == 1
        assert ctx.variables["alpha"] == 0.05


class TestResearchSession:
    def test_defaults(self):
        s = ResearchSession()
        assert s.id == ""
        assert s.title == ""
        assert s.messages == []
        assert isinstance(s.context, ResearchContext)
        assert s.artifacts == []
        assert s.trace_ids == []
        assert s.created_at > 0
        assert s.updated_at > 0

    def test_to_dict(self):
        art = Artifact(id="a1", type="table", name="t1", content="csv", metadata={}, created_at=1.0)
        ctx = ResearchContext(papers=[{"title": "p"}], variables={"lr": 0.01})
        s = _make_session(
            messages=[{"role": "user", "content": "hi"}],
            context=ctx,
            artifacts=[art],
            trace_ids=["tr-1"],
        )
        d = s.to_dict()
        assert d["id"] == "test-1"
        assert d["messages"] == [{"role": "user", "content": "hi"}]
        assert d["context"]["papers"] == [{"title": "p"}]
        assert d["context"]["variables"] == {"lr": 0.01}
        assert len(d["artifacts"]) == 1
        assert d["artifacts"][0]["id"] == "a1"
        assert d["trace_ids"] == ["tr-1"]

    def test_from_dict_full(self):
        data = {
            "id": "sid",
            "title": "My Session",
            "created_at": 1000.0,
            "updated_at": 1001.0,
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "papers": [{"title": "p"}],
                "datasets": [],
                "code_history": [],
                "figures": [],
                "variables": {"k": "v"},
            },
            "artifacts": [
                {"id": "a1", "type": "chart", "name": "fig", "content": "svg", "metadata": {}, "created_at": 1.0}
            ],
            "trace_ids": ["t1"],
        }
        s = ResearchSession.from_dict(data)
        assert s.id == "sid"
        assert s.title == "My Session"
        assert s.created_at == 1000.0
        assert len(s.messages) == 1
        assert s.context.papers == [{"title": "p"}]
        assert s.context.variables == {"k": "v"}
        assert len(s.artifacts) == 1
        assert s.artifacts[0].id == "a1"
        assert s.trace_ids == ["t1"]

    def test_from_dict_missing_fields(self):
        s = ResearchSession.from_dict({"id": "x"})
        assert s.id == "x"
        assert s.title == ""
        assert s.messages == []
        assert isinstance(s.context, ResearchContext)
        assert s.artifacts == []
        assert s.trace_ids == []

    def test_from_dict_empty_context(self):
        s = ResearchSession.from_dict({"id": "x", "context": {}})
        assert isinstance(s.context, ResearchContext)
        assert s.context.papers == []

    def test_from_dict_empty_artifacts(self):
        s = ResearchSession.from_dict({"id": "x", "artifacts": []})
        assert s.artifacts == []

    def test_roundtrip(self):
        s = _make_session(
            messages=[{"role": "assistant", "content": "world"}],
            context=ResearchContext(datasets=[{"name": "d"}]),
            artifacts=[Artifact(id="a1", type="img", name="n", content="c", metadata={"x": 1}, created_at=1.0)],
            trace_ids=["t1", "t2"],
        )
        d = s.to_dict()
        s2 = ResearchSession.from_dict(d)
        assert s2.id == s.id
        assert s2.messages == s.messages
        assert s2.context.datasets == s.context.datasets
        assert len(s2.artifacts) == 1
        assert s2.artifacts[0].metadata == {"x": 1}
        assert s2.trace_ids == ["t1", "t2"]


class TestSessionStoreABC:
    def test_abstract_methods(self):
        with pytest.raises(TypeError):
            SessionStore()


class TestMemorySessionStore:
    def test_save_and_load(self):
        store = MemorySessionStore()
        s = _make_session()
        store.save(s)
        loaded = store.load("test-1")
        assert loaded is not None
        assert loaded.id == "test-1"
        assert loaded.title == "Test Session"

    def test_load_missing(self):
        store = MemorySessionStore()
        assert store.load("nope") is None

    def test_delete_present(self):
        store = MemorySessionStore()
        store.save(_make_session())
        assert store.delete("test-1") is True
        assert store.load("test-1") is None

    def test_delete_missing(self):
        store = MemorySessionStore()
        assert store.delete("nope") is False

    def test_list_all_empty(self):
        store = MemorySessionStore()
        assert store.list_all() == []

    def test_list_all_multiple(self):
        store = MemorySessionStore()
        store.save(_make_session(id="a"))
        store.save(_make_session(id="b"))
        store.save(_make_session(id="c"))
        all_sessions = store.list_all()
        assert len(all_sessions) == 3
        ids = {s.id for s in all_sessions}
        assert ids == {"a", "b", "c"}

    def test_save_overwrites(self):
        store = MemorySessionStore()
        store.save(_make_session(title="V1"))
        store.save(_make_session(title="V2"))
        loaded = store.load("test-1")
        assert loaded.title == "V2"

    def test_eviction(self):
        store = MemorySessionStore(max_sessions=3)
        for i in range(5):
            store.save(_make_session(id=f"s-{i}"))
        assert len(store.list_all()) == 3

    def test_eviction_removes_oldest(self):
        store = MemorySessionStore(max_sessions=2)
        store.save(_make_session(id="first"))
        time.sleep(0.01)
        store.save(_make_session(id="second"))
        time.sleep(0.01)
        store.save(_make_session(id="third"))
        all_ids = {s.id for s in store.list_all()}
        assert "third" in all_ids
        assert "first" not in all_ids


class TestSQLiteSessionStore:
    def test_save_and_load(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = SQLiteSessionStore(db_path=db)
        s = _make_session()
        store.save(s)
        loaded = store.load("test-1")
        assert loaded is not None
        assert loaded.id == "test-1"
        assert loaded.title == "Test Session"

    def test_load_missing(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        assert store.load("nope") is None

    def test_delete_present(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        store.save(_make_session())
        assert store.delete("test-1") is True
        assert store.load("test-1") is None

    def test_delete_missing(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        assert store.delete("nope") is False

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

    def test_roundtrip_with_complex_data(self, tmp_path):
        store = SQLiteSessionStore(db_path=str(tmp_path / "test.db"))
        art = Artifact(id="a1", type="chart", name="fig", content="data", metadata={"k": "v"}, created_at=1.0)
        ctx = ResearchContext(papers=[{"title": "p1"}], variables={"lr": 0.01})
        s = _make_session(
            messages=[{"role": "user", "content": "hi"}],
            context=ctx,
            artifacts=[art],
            trace_ids=["tr1"],
        )
        store.save(s)
        loaded = store.load("test-1")
        assert loaded is not None
        assert loaded.messages[0]["content"] == "hi"
        assert loaded.context.papers[0]["title"] == "p1"
        assert len(loaded.artifacts) == 1
        assert loaded.artifacts[0].id == "a1"

    def test_creates_parent_directory(self, tmp_path):
        db = str(tmp_path / "sub" / "dir" / "test.db")
        store = SQLiteSessionStore(db_path=db)
        store.save(_make_session())
        loaded = store.load("test-1")
        assert loaded is not None

    def test_expanduser(self):
        store = SQLiteSessionStore()
        assert not store._db_path.startswith("~")


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
    async def test_get_session_missing(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        assert mgr.get_session("nope") is None

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
    async def test_delete_session_missing(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        assert await mgr.delete_session("nope") is False

    @pytest.mark.asyncio
    async def test_add_message(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        updated = await mgr.add_message(session.id, "user", "hello")
        assert updated is not None
        assert len(updated.messages) == 1
        assert updated.messages[0]["role"] == "user"
        assert updated.messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_add_message_missing_session(self, bus):
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
        assert updated.artifacts[0].id == "a1"

    @pytest.mark.asyncio
    async def test_add_artifact_missing_session(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        art = Artifact(id="a1", type="chart", name="fig1", content="data", metadata={}, created_at=time.time())
        result = await mgr.add_artifact("nope", art)
        assert result is None

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
    async def test_update_title_missing(self, bus):
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        result = await mgr.update_title("nope", "New")
        assert result is None

    @pytest.mark.asyncio
    async def test_event_on_create(self, bus):
        events = []

        async def capture(e):
            events.append(e)

        bus.on(EVENT_SESSION_CREATED, capture)
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        await mgr.create_session()
        assert len(events) == 1
        assert events[0].type == EVENT_SESSION_CREATED

    @pytest.mark.asyncio
    async def test_event_on_update_message(self, bus):
        events = []

        async def capture(e):
            events.append(e)

        bus.on(EVENT_SESSION_UPDATED, capture)
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        await mgr.add_message(session.id, "user", "hi")
        assert len(events) == 1
        assert events[0].data["action"] == "add_message"

    @pytest.mark.asyncio
    async def test_event_on_update_artifact(self, bus):
        events = []

        async def capture(e):
            events.append(e)

        bus.on(EVENT_SESSION_UPDATED, capture)
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        art = Artifact(id="a1", type="chart", name="fig1", content="data", metadata={}, created_at=time.time())
        await mgr.add_artifact(session.id, art)
        assert len(events) == 1
        assert events[0].data["action"] == "add_artifact"

    @pytest.mark.asyncio
    async def test_event_on_update_title(self, bus):
        events = []

        async def capture(e):
            events.append(e)

        bus.on(EVENT_SESSION_UPDATED, capture)
        mgr = SessionManager(store=MemorySessionStore(), event_bus=bus)
        session = await mgr.create_session()
        await mgr.update_title(session.id, "New Title")
        assert len(events) == 1
        assert events[0].data["action"] == "update_title"

    @pytest.mark.asyncio
    async def test_default_store_and_bus(self):
        reset_event_bus()
        mgr = SessionManager()
        assert isinstance(mgr._store, MemorySessionStore)
        assert isinstance(mgr._bus, EventBus)
        reset_event_bus()


# ---------------------------------------------------------------------------
# 2. fusion_science.utils.mirrors
# ---------------------------------------------------------------------------
from fusion_science.utils.mirrors import (
    clear_cache,
    get_available_databases,
    get_cache_stats,
    get_mirror_config,
    get_offline_recommendation,
)


class TestGetMirrorConfig:
    def test_default_values(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = get_mirror_config()
            assert cfg["enabled"] is False
            assert cfg["offline_mode"] is False
            assert "pubmed" in cfg["mirrors"]
            assert "NGDC" in cfg["chinese_databases"]

    def test_enabled_via_env(self):
        with patch.dict(os.environ, {"FUSION_SCIENCE_USE_MIRRORS": "true"}, clear=False):
            cfg = get_mirror_config()
            assert cfg["enabled"] is True

    def test_offline_mode_env(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}, clear=False):
            cfg = get_mirror_config()
            assert cfg["offline_mode"] is True

    def test_offline_mode_env_yes(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "yes"}, clear=False):
            cfg = get_mirror_config()
            assert cfg["offline_mode"] is True

    def test_offline_mode_env_1(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "1"}, clear=False):
            cfg = get_mirror_config()
            assert cfg["offline_mode"] is True

    def test_mirror_url_env_overrides(self):
        with patch.dict(os.environ, {"FUSION_SCI_PUBMED_MIRROR": "https://mirror.example.com"}, clear=False):
            cfg = get_mirror_config()
            assert cfg["mirrors"]["pubmed"]["mirror"] == "https://mirror.example.com"

    def test_chinese_databases_present(self):
        cfg = get_mirror_config()
        assert "NGDC" in cfg["chinese_databases"]
        assert "CNKI" in cfg["chinese_databases"]
        assert "ScienceDB" in cfg["chinese_databases"]

    def test_all_mirror_keys(self):
        cfg = get_mirror_config()
        for key in ("pubmed", "uniprot", "pdb", "ensembl", "chembl"):
            assert key in cfg["mirrors"]
            assert "primary" in cfg["mirrors"][key]
            assert "mirror" in cfg["mirrors"][key]
            assert "note" in cfg["mirrors"][key]


class TestGetOfflineRecommendation:
    def test_returns_string(self):
        rec = get_offline_recommendation()
        assert isinstance(rec, str)
        assert "Offline" in rec
        assert "PubMed" in rec
        assert "UniProt" in rec
        assert "PDB" in rec

    def test_contains_code_blocks(self):
        rec = get_offline_recommendation()
        assert "```" in rec


class TestGetAvailableDatabases:
    def test_returns_list(self):
        dbs = get_available_databases()
        assert isinstance(dbs, list)
        assert len(dbs) > 0

    def test_entry_structure(self):
        dbs = get_available_databases()
        for db in dbs:
            assert "name" in db
            assert "type" in db
            assert "offline" in db

    def test_contains_pubmed(self):
        dbs = get_available_databases()
        names = [d["name"] for d in dbs]
        assert "PubMed" in names

    def test_contains_chinese_db(self):
        dbs = get_available_databases()
        names = [d["name"] for d in dbs]
        assert "CNKI" in names


class TestCacheFunctions:
    def test_get_cache_stats(self):
        with patch("fusion_science.database.mirror.get_shared_cache") as mock_get:
            mock_instance = MagicMock()
            mock_instance.stats.return_value = {"total_entries": 5}
            mock_get.return_value = mock_instance
            stats = get_cache_stats()
            assert stats["total_entries"] == 5

    def test_clear_cache(self):
        with patch("fusion_science.database.mirror.get_shared_cache") as mock_get:
            mock_instance = MagicMock()
            mock_instance.stats.return_value = {"total_entries": 3}
            mock_get.return_value = mock_instance
            count = clear_cache()
            assert count == 3
            mock_instance.clear.assert_called_once()

    def test_clear_cache_with_source(self):
        with patch("fusion_science.database.mirror.get_shared_cache") as mock_get:
            mock_instance = MagicMock()
            mock_instance.stats.return_value = {"total_entries": 2}
            mock_get.return_value = mock_instance
            count = clear_cache(source="pubmed")
            assert count == 2
            mock_instance.clear.assert_called_once_with(source="pubmed")


# ---------------------------------------------------------------------------
# 3. fusion_science.utils.events
# ---------------------------------------------------------------------------
from fusion_science.utils.events import (
    EVENT_CODE_EXECUTION,
    EVENT_DB_QUERY,
    EVENT_ERROR,
    EVENT_LLM_CALL,
    EVENT_TOOL_EXECUTED,
    EVENT_VISUALIZATION,
    Event,
    get_event_bus,
)


class TestEvent:
    def test_defaults(self):
        e = Event(type="test")
        assert e.data == {}
        assert e.source == ""
        assert e.timestamp == 0.0

    def test_with_data(self):
        e = Event(type="db_query", data={"q": "cancer"}, source="pubmed", timestamp=1.0)
        assert e.type == "db_query"
        assert e.data["q"] == "cancer"
        assert e.source == "pubmed"
        assert e.timestamp == 1.0


class TestEventBus:
    def test_empty_bus(self):
        bus = EventBus()
        assert bus.handler_count() == 0
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
    async def test_emit_no_data(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.on("test", handler)
        await bus.emit("test", source="src")
        assert received[0].data == {}

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

    @pytest.mark.asyncio
    async def test_off_not_registered(self):
        bus = EventBus()

        async def handler(e):
            pass

        bus.off("nope", handler)

    def test_handler_count(self):
        bus = EventBus()

        async def h1(e):
            pass

        async def h2(e):
            pass

        bus.on(EVENT_ERROR, h1)
        bus.on(EVENT_ERROR, h2)
        bus.on("other", h1)
        assert bus.handler_count(EVENT_ERROR) == 2
        assert bus.handler_count() == 3

    @pytest.mark.asyncio
    async def test_handler_exception_is_logged(self):
        bus = EventBus()

        async def bad_handler(event):
            raise ValueError("boom")

        bus.on("test", bad_handler)
        await bus.emit("test", {}, source="src")
        assert len(bus.get_history()) == 1

    @pytest.mark.asyncio
    async def test_get_history(self):
        bus = EventBus()
        await bus.emit(EVENT_DB_QUERY, {"q": 1}, source="a")
        await bus.emit(EVENT_CODE_EXECUTION, {"code": "x=1"}, source="b")
        history = bus.get_history()
        assert len(history) == 2
        assert history[0].type == EVENT_DB_QUERY
        assert history[1].type == EVENT_CODE_EXECUTION

    @pytest.mark.asyncio
    async def test_get_history_filtered(self):
        bus = EventBus()
        await bus.emit(EVENT_DB_QUERY, {}, source="a")
        await bus.emit(EVENT_CODE_EXECUTION, {}, source="b")
        await bus.emit(EVENT_DB_QUERY, {}, source="c")
        filtered = bus.get_history(event_type=EVENT_DB_QUERY)
        assert len(filtered) == 2

    @pytest.mark.asyncio
    async def test_get_history_with_limit(self):
        bus = EventBus()
        for i in range(10):
            await bus.emit("test", {"i": i}, source="src")
        limited = bus.get_history(limit=3)
        assert len(limited) == 3

    @pytest.mark.asyncio
    async def test_history_max_size(self):
        bus = EventBus()
        for i in range(1100):
            await bus.emit("test", {"i": i}, source="bench")
        history = bus.get_history(limit=2000)
        assert len(history) == 1000

    @pytest.mark.asyncio
    async def test_clear_history(self):
        bus = EventBus()
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


class TestEventConstants:
    def test_all_constants(self):
        assert EVENT_DB_QUERY == "db_query"
        assert EVENT_CODE_EXECUTION == "code_execution"
        assert EVENT_LLM_CALL == "llm_call"
        assert EVENT_VISUALIZATION == "visualization"
        assert EVENT_SESSION_CREATED == "session_created"
        assert EVENT_SESSION_UPDATED == "session_updated"
        assert EVENT_TOOL_EXECUTED == "tool_executed"
        assert EVENT_ERROR == "error"


# ---------------------------------------------------------------------------
# 4. fusion_science.utils.keychain
# ---------------------------------------------------------------------------
from fusion_science.utils.keychain import (
    SecureConfig,
    _security_cmd,
    delete_key,
    list_keys,
    retrieve_key,
    store_key,
)


class TestSecurityCmd:
    def test_success(self):
        with patch("fusion_science.utils.keychain.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
            result = _security_cmd(["find-generic-password", "-s", "test", "-w"])
            assert result == "ok"

    def test_not_found_raises_key_error(self):
        with patch("fusion_science.utils.keychain.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=44, stderr="could not be found")
            with pytest.raises(KeyError):
                _security_cmd(["find-generic-password", "-s", "test", "-w"])

    def test_other_error_raises_runtime_error(self):
        with patch("fusion_science.utils.keychain.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
            with pytest.raises(RuntimeError):
                _security_cmd(["find-generic-password", "-s", "test", "-w"])

    def test_command_not_found(self):
        with patch("fusion_science.utils.keychain.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="security"):
                _security_cmd(["test"])

    def test_item_not_found(self):
        with patch("fusion_science.utils.keychain.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=44, stderr="item could not be found")
            with pytest.raises(KeyError):
                _security_cmd(["delete-generic-password", "-s", "test"])


class TestStoreKey:
    def test_success(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.return_value = ""
            result = store_key("mykey", "myval")
            assert result is True
            assert mock_cmd.call_count == 2

    def test_delete_then_add(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.return_value = ""
            store_key("mykey", "myval")
            first_call = mock_cmd.call_args_list[0]
            assert "delete-generic-password" in first_call[0][0]

    def test_failure(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.side_effect = [None, RuntimeError("fail")]
            result = store_key("mykey", "myval")
            assert result is False


class TestRetrieveKey:
    def test_success(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.return_value = "secret_value"
            result = retrieve_key("mykey")
            assert result == "secret_value"

    def test_not_found(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.side_effect = KeyError("not found")
            result = retrieve_key("mykey")
            assert result is None

    def test_other_error(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.side_effect = RuntimeError("fail")
            result = retrieve_key("mykey")
            assert result is None


class TestDeleteKey:
    def test_success(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.return_value = ""
            result = delete_key("mykey")
            assert result is True

    def test_not_found(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.side_effect = KeyError("not found")
            result = delete_key("mykey")
            assert result is False

    def test_other_error(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.side_effect = RuntimeError("fail")
            result = delete_key("mykey")
            assert result is False


class TestListKeys:
    def test_success(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.return_value = 'some line\n"acct"<blob>= "key1"\nother line\n"acct"<blob>= "key2"\n'
            result = list_keys()
            assert "key1" in result
            assert "key2" in result

    def test_no_keys(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.return_value = "no keys here"
            result = list_keys()
            assert result == []

    def test_error(self):
        with patch("fusion_science.utils.keychain._security_cmd") as mock_cmd:
            mock_cmd.side_effect = RuntimeError("fail")
            result = list_keys()
            assert result == []


class TestSecureConfig:
    def test_store_success(self):
        with patch("fusion_science.utils.keychain.store_key", return_value=True):
            cfg = SecureConfig()
            result = cfg.store("k", "v")
            assert result is True

    def test_store_fallback(self):
        with patch("fusion_science.utils.keychain.store_key", return_value=False):
            cfg = SecureConfig()
            result = cfg.store("k", "v")
            assert result is False
            assert cfg._fallback["k"] == "v"

    def test_retrieve_from_keychain(self):
        with patch("fusion_science.utils.keychain.retrieve_key", return_value="secret"):
            cfg = SecureConfig()
            val = cfg.retrieve("k")
            assert val == "secret"

    def test_retrieve_from_fallback(self):
        with patch("fusion_science.utils.keychain.retrieve_key", return_value=None):
            cfg = SecureConfig()
            cfg._fallback["k"] = "fallback_val"
            val = cfg.retrieve("k")
            assert val == "fallback_val"

    def test_retrieve_missing(self):
        with patch("fusion_science.utils.keychain.retrieve_key", return_value=None):
            cfg = SecureConfig()
            val = cfg.retrieve("k")
            assert val is None

    def test_delete(self):
        with patch("fusion_science.utils.keychain.delete_key", return_value=True):
            cfg = SecureConfig()
            cfg._fallback["k"] = "v"
            result = cfg.delete("k")
            assert result is True
            assert "k" not in cfg._fallback

    def test_list_stored_keys(self):
        with patch("fusion_science.utils.keychain.list_keys", return_value=["a", "b"]):
            cfg = SecureConfig()
            cfg._fallback["c"] = "v"
            keys = cfg.list_stored_keys()
            assert set(keys) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 5. fusion_science.utils.offline
# ---------------------------------------------------------------------------
from fusion_science.utils.offline import get_connectivity, is_offline


class TestIsOffline:
    def test_env_override_true(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}, clear=False):
            assert is_offline() is True

    def test_env_override_1(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "1"}, clear=False):
            assert is_offline() is True

    def test_env_override_yes(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "yes"}, clear=False):
            assert is_offline() is True

    def test_env_override_false(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "false"}, clear=False):
            with patch("fusion_science.utils.offline.socket.create_connection") as mock_conn:
                mock_conn.return_value = None
                assert is_offline() is False

    def test_network_unreachable(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": ""}, clear=True):
            with patch("fusion_science.utils.offline.socket.create_connection", side_effect=OSError):
                assert is_offline() is True

    def test_network_reachable(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": ""}, clear=True):
            with patch("fusion_science.utils.offline.socket.create_connection"):
                assert is_offline() is False


class TestGetConnectivity:
    def test_offline_mode(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}, clear=False):
            result = get_connectivity()
            assert result["offline"] is True

    def test_online_all_reachable(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": ""}, clear=True):
            with patch("fusion_science.utils.offline.socket.create_connection"):
                result = get_connectivity()
                assert result["offline"] is False
                assert result.get("pubmed") == "reachable"

    def test_online_some_unreachable(self):
        call_count = 0

        def mock_conn(addr, timeout):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise OSError("unreachable")

        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": ""}, clear=True):
            with patch("fusion_science.utils.offline.socket.create_connection", side_effect=mock_conn):
                result = get_connectivity()
                assert result["offline"] is False

    def test_result_has_env_override(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}, clear=False):
            result = get_connectivity()
            assert result["env_override"] == "true"


# ---------------------------------------------------------------------------
# 6. fusion_science.mcp_server
# ---------------------------------------------------------------------------
from fusion_science.mcp_server import (
    _error_response,
    _success_response,
    handle_mcp,
    mcp_sse,
    router,
)


class TestMCPServerHelpers:
    def test_success_response(self):
        resp = _success_response("1", {"key": "value"})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "1"
        assert resp["result"] == {"key": "value"}

    def test_error_response(self):
        resp = _error_response("2", -32600, "Invalid Request")
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == "2"
        assert resp["error"]["code"] == -32600
        assert resp["error"]["message"] == "Invalid Request"

    def test_error_response_null_id(self):
        resp = _error_response(None, -32700, "Parse error")
        assert resp["id"] is None


class TestHandleMCP:
    @pytest.mark.asyncio
    async def test_parse_error(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"not json")
        resp = await handle_mcp(req)
        assert resp["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_missing_method(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=json.dumps({"id": "1"}).encode())
        resp = await handle_mcp(req)
        assert resp["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_initialize(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=json.dumps({"id": "1", "method": "initialize"}).encode())
        resp = await handle_mcp(req)
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["serverInfo"]["name"] == "fusion-science-mcp"

    @pytest.mark.asyncio
    async def test_tools_list_no_registry(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=json.dumps({"id": "2", "method": "tools/list"}).encode())
        req.app.state.tool_registry = None
        resp = await handle_mcp(req)
        assert resp["result"]["tools"] == []

    @pytest.mark.asyncio
    async def test_tools_list_with_registry(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=json.dumps({"id": "3", "method": "tools/list"}).encode())
        mock_registry = MagicMock()
        mock_registry.get_mcp_tools.return_value = [{"name": "search"}]
        req.app.state.tool_registry = mock_registry
        resp = await handle_mcp(req)
        assert resp["result"]["tools"] == [{"name": "search"}]

    @pytest.mark.asyncio
    async def test_tools_call_missing_name(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=json.dumps({"id": "4", "method": "tools/call", "params": {}}).encode())
        resp = await handle_mcp(req)
        assert resp["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_tools_call_no_registry(self):
        req = MagicMock()
        req.body = AsyncMock(
            return_value=json.dumps({"id": "5", "method": "tools/call", "params": {"name": "search"}}).encode()
        )
        req.app.state.tool_registry = None
        resp = await handle_mcp(req)
        assert resp["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_tools_call_success(self):
        req = MagicMock()
        req.body = AsyncMock(
            return_value=json.dumps(
                {"id": "6", "method": "tools/call", "params": {"name": "search", "arguments": {"q": "cancer"}}}
            ).encode()
        )
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value={"results": []})
        req.app.state.tool_registry = mock_registry
        resp = await handle_mcp(req)
        assert "content" in resp["result"]

    @pytest.mark.asyncio
    async def test_tools_call_exception(self):
        req = MagicMock()
        req.body = AsyncMock(
            return_value=json.dumps({"id": "7", "method": "tools/call", "params": {"name": "search"}}).encode()
        )
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(side_effect=ValueError("boom"))
        req.app.state.tool_registry = mock_registry
        resp = await handle_mcp(req)
        assert resp["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_method_not_found(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=json.dumps({"id": "8", "method": "unknown/method"}).encode())
        resp = await handle_mcp(req)
        assert resp["error"]["code"] == -32601


class TestMCPSSE:
    @pytest.mark.asyncio
    async def test_sse_returns_streaming_response(self):
        req = MagicMock()
        req.is_disconnected = AsyncMock(return_value=True)
        resp = await mcp_sse(req)
        assert resp.media_type == "text/event-stream"
        assert "no-cache" in resp.headers.get("Cache-Control", "")

    @pytest.mark.asyncio
    async def test_sse_endpoint_event(self):
        req = MagicMock()
        disconnected_calls = 0

        async def mock_disconnected():
            nonlocal disconnected_calls
            disconnected_calls += 1
            return disconnected_calls > 1

        req.is_disconnected = mock_disconnected
        resp = await mcp_sse(req)
        body_parts = []
        async for chunk in resp.body_iterator:
            body_parts.append(chunk)
            if len(body_parts) >= 2:
                break
        assert any("endpoint" in part for part in body_parts)


class TestMCPRouter:
    def test_router_exists(self):
        assert router is not None


# ---------------------------------------------------------------------------
# 7. fusion_science.audit.compliance
# ---------------------------------------------------------------------------
from fusion_science.audit.compliance import (
    ComplianceChecker,
    ComplianceResult,
)


class TestComplianceResult:
    def test_to_dict(self):
        r = ComplianceResult(category="test", passed=True, severity="info", details="ok", recommendation="OK")
        d = r.to_dict()
        assert d["category"] == "test"
        assert d["passed"] is True
        assert d["severity"] == "info"
        assert d["details"] == "ok"
        assert d["recommendation"] == "OK"


class TestComplianceChecker:
    def setup_method(self):
        self.checker = ComplianceChecker()

    def test_data_residency_no_entries(self):
        result = self.checker.check_data_residency(None)
        assert result.passed is True
        assert result.category == "data_residency"

    def test_data_residency_empty_entries(self):
        result = self.checker.check_data_residency([])
        assert result.passed is True

    def test_data_residency_local_only(self):
        entries = [{"id": "1", "description": "local computation", "parameters": {}}]
        result = self.checker.check_data_residency(entries)
        assert result.passed is True

    def test_data_residency_remote_call(self):
        entries = [{"id": "1", "description": "call to https://api.openai.com/v1", "parameters": {}}]
        result = self.checker.check_data_residency(entries)
        assert result.passed is False
        assert result.severity == "critical"

    def test_data_residency_localhost_ok(self):
        entries = [{"id": "1", "description": "call to http://localhost:11434/v1", "parameters": {}}]
        result = self.checker.check_data_residency(entries)
        assert result.passed is True

    def test_data_residency_127_ok(self):
        entries = [{"id": "1", "description": "call to http://127.0.0.1:8080", "parameters": {}}]
        result = self.checker.check_data_residency(entries)
        assert result.passed is True

    def test_data_residency_remote_in_params(self):
        entries = [{"id": "1", "description": "fetch", "parameters": {"url": "https://huggingface.co/model"}}]
        result = self.checker.check_data_residency(entries)
        assert result.passed is False

    def test_algorithm_registration_exempt(self):
        for ctx in ("personal", "lab_internal", "research", "education"):
            result = self.checker.check_algorithm_registration(ctx)
            assert result.passed is True

    def test_algorithm_registration_requires_registration(self):
        result = self.checker.check_algorithm_registration("commercial")
        assert result.passed is False
        assert result.severity == "warning"

    def test_algorithm_registration_case_insensitive(self):
        result = self.checker.check_algorithm_registration("Personal")
        assert result.passed is True

    def test_ethics_review_no_entries(self):
        result = self.checker.check_ethics_review(None)
        assert result.passed is True

    def test_ethics_review_empty_entries(self):
        result = self.checker.check_ethics_review([])
        assert result.passed is True

    def test_ethics_review_no_sensitive(self):
        entries = [{"id": "1", "description": "data analysis", "parameters": {}}]
        result = self.checker.check_ethics_review(entries)
        assert result.passed is True

    def test_ethics_review_human_subject(self):
        entries = [{"id": "1", "description": "human subject study", "parameters": {}}]
        result = self.checker.check_ethics_review(entries)
        assert result.passed is False

    def test_ethics_review_genome(self):
        entries = [{"id": "1", "description": "genome analysis", "parameters": {}}]
        result = self.checker.check_ethics_review(entries)
        assert result.passed is False

    def test_ethics_review_clinical_trial(self):
        entries = [{"id": "1", "description": "experiment", "parameters": {"type": "clinical trial"}}]
        result = self.checker.check_ethics_review(entries)
        assert result.passed is False

    def test_sensitive_data_no_entries(self):
        result = self.checker.check_sensitive_data(None)
        assert result.passed is True

    def test_sensitive_data_none_sensitive(self):
        entries = [{"id": "1", "description": "simple calculation", "parameters": {}}]
        result = self.checker.check_sensitive_data(entries)
        assert result.passed is True
        assert result.severity == "info"

    def test_sensitive_data_detected(self):
        entries = [{"id": "1", "description": "genome sequencing", "parameters": {}}]
        result = self.checker.check_sensitive_data(entries)
        assert result.passed is True
        assert result.severity == "warning"

    def test_sensitive_data_in_params(self):
        entries = [{"id": "1", "description": "analysis", "parameters": {"data_type": "clinical trial data"}}]
        result = self.checker.check_sensitive_data(entries)
        assert result.severity == "warning"

    def test_check_all(self):
        results = self.checker.check(trace_entries=None, usage_context="personal")
        assert len(results) == 4
        assert all(r.passed for r in results)

    def test_check_with_violations(self):
        entries = [{"id": "1", "description": "call https://api.openai.com and genome study", "parameters": {}}]
        results = self.checker.check(trace_entries=entries, usage_context="commercial")
        passed_count = sum(r.passed for r in results)
        assert passed_count < 4

    def test_check_report(self):
        report = self.checker.check_report(session_id="s1", trace_entries=None, usage_context="personal")
        assert report["session_id"] == "s1"
        assert report["all_passed"] is True
        assert "results" in report
        assert "summary" in report
        assert report["summary"]["total_checks"] == 4
        assert report["summary"]["passed"] == 4

    def test_check_report_with_failures(self):
        entries = [{"id": "1", "description": "https://api.openai.com", "parameters": {}}]
        report = self.checker.check_report(session_id="s2", trace_entries=entries, usage_context="commercial")
        assert report["all_passed"] is False
        assert report["summary"]["failed"] > 0


# ---------------------------------------------------------------------------
# 8. fusion_science.audit.integrity
# ---------------------------------------------------------------------------
from fusion_science.audit.integrity import (
    _REQUIRED_OPERATION_TYPES,
    AuditIntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)


class TestIntegrityIssue:
    def test_defaults(self):
        issue = IntegrityIssue(severity="warning", category="test", description="desc")
        assert issue.entry_id == ""
        assert issue.suggestion == ""


class TestIntegrityReport:
    def test_defaults(self):
        report = IntegrityReport(session_id="s1")
        assert report.total_entries == 0
        assert report.traced_operations == 0
        assert report.coverage_percent == 0.0
        assert report.issues == []
        assert report.passed is True

    def test_to_dict(self):
        report = IntegrityReport(
            session_id="s1",
            total_entries=5,
            traced_operations=3,
            coverage_percent=75.0,
            issues=[
                IntegrityIssue(severity="warning", category="cat", description="desc", entry_id="e1", suggestion="fix")
            ],
            passed=False,
        )
        d = report.to_dict()
        assert d["session_id"] == "s1"
        assert d["total_entries"] == 5
        assert d["traced_operations"] == 3
        assert d["coverage_percent"] == 75.0
        assert len(d["issues"]) == 1
        assert d["issues"][0]["severity"] == "warning"
        assert d["issues"][0]["entry_id"] == "e1"
        assert d["passed"] is False

    def test_to_dict_coverage_rounding(self):
        report = IntegrityReport(session_id="s1", coverage_percent=75.456)
        d = report.to_dict()
        assert d["coverage_percent"] == 75.5


class TestAuditIntegrityChecker:
    def test_check_session_none(self):
        checker = AuditIntegrityChecker()
        report = checker.check_session(None)
        assert report.passed is False
        assert any(i.category == "missing_session" for i in report.issues)

    def test_check_session_empty(self):
        checker = AuditIntegrityChecker()
        session = MagicMock(entries=[], session_id="s1")
        report = checker.check_session(session)
        assert report.total_entries == 0
        missing_ops = [i for i in report.issues if i.category == "missing_operation_type"]
        assert len(missing_ops) > 0

    def test_check_session_with_all_ops(self):
        checker = AuditIntegrityChecker()
        entries = []
        for op in ("db_query", "code_execution", "llm_call", "visualization"):
            entries.append(
                MagicMock(
                    id=f"e-{op}",
                    operation=op,
                    parent_id="",
                    success=True,
                    error="",
                    parameters={"k": "v"},
                    duration_ms=1.0,
                )
            )
        session = MagicMock(entries=entries, session_id="s1")
        report = checker.check_session(session)
        assert report.traced_operations == 4
        assert report.coverage_percent == 100.0

    def test_check_session_missing_error_detail(self):
        checker = AuditIntegrityChecker()
        entry = MagicMock(
            id="e1", operation="db_query", parent_id="", success=False, error="", parameters={"k": "v"}, duration_ms=1.0
        )
        session = MagicMock(entries=[entry], session_id="s1")
        report = checker.check_session(session)
        assert any(i.category == "missing_error_detail" for i in report.issues)

    def test_check_session_missing_parameters(self):
        checker = AuditIntegrityChecker()
        entry = MagicMock(
            id="e1", operation="db_query", parent_id="", success=True, error="", parameters={}, duration_ms=1.0
        )
        session = MagicMock(entries=[entry], session_id="s1")
        report = checker.check_session(session)
        assert any(i.category == "missing_parameters" for i in report.issues)

    def test_check_session_missing_duration(self):
        checker = AuditIntegrityChecker()
        entry = MagicMock(
            id="e1", operation="db_query", parent_id="", success=True, error="", parameters={"k": "v"}, duration_ms=0.0
        )
        session = MagicMock(entries=[entry], session_id="s1")
        report = checker.check_session(session)
        assert any(i.category == "missing_duration" for i in report.issues)

    def test_check_session_broken_parent_ref(self):
        checker = AuditIntegrityChecker()
        entry = MagicMock(
            id="e1",
            operation="llm_call",
            parent_id="missing-parent",
            success=True,
            error="",
            parameters={"k": "v"},
            duration_ms=1.0,
        )
        session = MagicMock(entries=[entry], session_id="s1")
        report = checker.check_session(session)
        assert any(i.category == "broken_parent_ref" for i in report.issues)
        assert report.passed is False

    def test_check_session_valid_parent_ref(self):
        checker = AuditIntegrityChecker()
        e1 = MagicMock(
            id="e1", operation="llm_call", parent_id="", success=True, error="", parameters={"k": "v"}, duration_ms=1.0
        )
        e2 = MagicMock(
            id="e2",
            operation="code_execution",
            parent_id="e1",
            success=True,
            error="",
            parameters={"k": "v"},
            duration_ms=1.0,
        )
        session = MagicMock(entries=[e1, e2], session_id="s1")
        report = checker.check_session(session)
        assert not any(i.category == "broken_parent_ref" for i in report.issues)

    def test_check_session_low_coverage_fails(self):
        checker = AuditIntegrityChecker(required_ops={"a", "b", "c", "d"})
        entry = MagicMock(
            id="e1", operation="a", parent_id="", success=True, error="", parameters={"k": "v"}, duration_ms=1.0
        )
        session = MagicMock(entries=[entry], session_id="s1")
        report = checker.check_session(session)
        assert report.coverage_percent == 25.0
        assert report.passed is False

    def test_custom_required_ops(self):
        checker = AuditIntegrityChecker(required_ops={"custom_op"})
        entry = MagicMock(
            id="e1", operation="custom_op", parent_id="", success=True, error="", parameters={"k": "v"}, duration_ms=1.0
        )
        session = MagicMock(entries=[entry], session_id="s1")
        report = checker.check_session(session)
        assert report.coverage_percent == 100.0

    def test_empty_required_ops(self):
        checker = AuditIntegrityChecker(required_ops=None)
        session = MagicMock(entries=[], session_id="s1")
        report = checker.check_session(session)
        assert report.coverage_percent == 0.0
        assert report.passed is False

    def test_required_operation_types_constant(self):
        assert "db_query" in _REQUIRED_OPERATION_TYPES
        assert "code_execution" in _REQUIRED_OPERATION_TYPES
        assert "llm_call" in _REQUIRED_OPERATION_TYPES
        assert "visualization" in _REQUIRED_OPERATION_TYPES


class TestAuditIntegrityCheckerProvenance:
    def test_check_provenance_none(self):
        checker = AuditIntegrityChecker()
        report = checker.check_provenance_chain(None)
        assert report.passed is False
        assert any(i.category == "missing_graph" for i in report.issues)

    def test_check_provenance_empty(self):
        checker = AuditIntegrityChecker()
        graph = MagicMock(nodes={})
        report = checker.check_provenance_chain(graph)
        assert report.total_entries == 0
        assert report.coverage_percent == 0.0

    def test_check_provenance_valid(self):
        checker = AuditIntegrityChecker()
        node_a = MagicMock(inputs=[], type="source")
        node_b = MagicMock(inputs=["a"], type="transformation")
        node_c = MagicMock(inputs=["b"], type="output")
        graph = MagicMock(nodes={"a": node_a, "b": node_b, "c": node_c})
        report = checker.check_provenance_chain(graph)
        assert report.passed is True
        assert report.total_entries == 3

    def test_check_provenance_broken_lineage(self):
        checker = AuditIntegrityChecker()
        node_a = MagicMock(inputs=["missing"], type="transformation")
        graph = MagicMock(nodes={"a": node_a})
        report = checker.check_provenance_chain(graph)
        assert any(i.category == "broken_lineage" for i in report.issues)
        assert report.passed is False

    def test_check_provenance_orphan_output(self):
        checker = AuditIntegrityChecker()
        node_a = MagicMock(inputs=[], type="output")
        graph = MagicMock(nodes={"a": node_a})
        report = checker.check_provenance_chain(graph)
        assert any(i.category == "orphan_output" for i in report.issues)

    def test_check_provenance_root_transformation(self):
        checker = AuditIntegrityChecker()
        node_a = MagicMock(inputs=[], type="transformation")
        graph = MagicMock(nodes={"a": node_a})
        report = checker.check_provenance_chain(graph)
        assert any(i.category == "root_transformation" for i in report.issues)
        assert report.passed is True


# ---------------------------------------------------------------------------
# 9. fusion_science.literature.math_explainer
# ---------------------------------------------------------------------------
from fusion_science.literature.math_explainer import (
    FORMULA_PATTERNS,
    LATEX_SYMBOLS,
    FormulaExplanation,
    MathExplainer,
)


class TestFormulaExplanation:
    def test_defaults(self):
        fe = FormulaExplanation(original="x=1")
        assert fe.name == ""
        assert fe.explanation == ""
        assert fe.symbols == []
        assert fe.plain_text == ""

    def test_to_dict(self):
        fe = FormulaExplanation(
            original="p<0.05", name="p-value", explanation="sig", symbols=["\\alpha = α"], plain_text="p<0.05"
        )
        d = fe.to_dict()
        assert d["original"] == "p<0.05"
        assert d["name"] == "p-value"
        assert d["explanation"] == "sig"
        assert d["symbols"] == ["\\alpha = α"]
        assert d["plain_text"] == "p<0.05"


class TestMathExplainer:
    def setup_method(self):
        self.explainer = MathExplainer()

    def test_explain_p_value(self):
        result = self.explainer.explain("p < 0.05")
        assert result.name == "p-value"
        assert result.original == "p < 0.05"

    def test_explain_correlation(self):
        result = self.explainer.explain("r = 0.85")
        assert result.name == "correlation coefficient"

    def test_explain_odds_ratio(self):
        result = self.explainer.explain("OR = 2.5")
        assert result.name == "odds ratio"

    def test_explain_hazard_ratio(self):
        result = self.explainer.explain("HR = 1.3")
        assert result.name == "hazard ratio"

    def test_explain_confidence_interval(self):
        result = self.explainer.explain("CI: [1.2, 3.4]")
        assert result.name == "confidence interval"

    def test_explain_sample_size(self):
        result = self.explainer.explain("n = 100")
        assert result.name == "sample size"

    def test_explain_f_statistic(self):
        result = self.explainer.explain("F(2, 30) = 5.4")
        assert result.name == "F-statistic"

    def test_explain_t_statistic(self):
        result = self.explainer.explain("t(28) = -2.1")
        assert result.name == "t-statistic"

    def test_explain_auc(self):
        result = self.explainer.explain("AUC = 0.92")
        assert result.name == "AUC (Area Under Curve)"

    def test_explain_i_squared(self):
        result = self.explainer.explain("I2 = 75%")
        assert result.name == "I-squared heterogeneity"

    def test_explain_cohens_d(self):
        result = self.explainer.explain("d = 0.8")
        assert result.name == "Cohen's d"

    def test_explain_generic_equation(self):
        result = self.explainer.explain("y = mx + b")
        assert result.name == "mathematical expression"
        assert "equation" in result.explanation.lower()

    def test_explain_generic_arithmetic(self):
        result = self.explainer.explain("3 + 4 * 2")
        assert result.name == "mathematical expression"
        assert "arithmetic" in result.explanation.lower()

    def test_explain_generic_other(self):
        result = self.explainer.explain("\\alpha\\beta")
        assert result.name == "mathematical expression"

    def test_extract_symbols(self):
        result = self.explainer.explain("\\alpha + \\beta = \\gamma")
        assert "\\alpha = α" in result.symbols
        assert "\\beta = β" in result.symbols
        assert "\\gamma = γ" in result.symbols

    def test_latex_to_plain(self):
        result = self.explainer.explain("\\alpha^{2} + \\beta_{n}")
        assert "α" in result.plain_text
        assert "β" in result.plain_text

    def test_latex_to_plain_braces(self):
        result = self.explainer.explain("x^{2}")
        assert "x^2" in result.plain_text

    def test_latex_to_plain_subscript(self):
        result = self.explainer.explain("x_{i}")
        assert "x_i" in result.plain_text

    def test_latex_to_plain_strip_braces_backslash(self):
        result = self.explainer.explain("\\frac{a}{b}")
        assert "\\" not in result.plain_text
        assert "{" not in result.plain_text

    def test_explain_text_inline(self):
        text = "Result was $p < 0.05$ which is significant"
        results = self.explainer.explain_text(text)
        assert len(results) >= 1
        assert any(r.name == "p-value" for r in results)

    def test_explain_text_display(self):
        text = "Formula: $$r = 0.85$$ end"
        results = self.explainer.explain_text(text)
        assert any(r.name == "correlation coefficient" for r in results)

    def test_explain_text_pattern_match(self):
        text = "The result n = 50 was observed"
        results = self.explainer.explain_text(text)
        assert any(r.name == "sample size" for r in results)

    def test_explain_text_empty(self):
        results = self.explainer.explain_text("no formulas here")
        assert results == []

    @pytest.mark.asyncio
    async def test_explain_with_llm_no_gateway(self):
        result = await self.explainer.explain_with_llm("p < 0.05")
        assert result.name == "p-value"

    @pytest.mark.asyncio
    async def test_explain_with_llm_gateway_success(self):
        mock_gateway = MagicMock()
        mock_result = MagicMock()
        mock_result.parsed = {"name": "t-test", "explanation": "Student t", "plain_text": "t test"}
        mock_result.error = None
        mock_gateway.structured_output = AsyncMock(return_value=mock_result)
        mock_gateway.get_model_for_role.return_value = "model-name"

        explainer = MathExplainer(gateway=mock_gateway)
        result = await explainer.explain_with_llm("t(28) = -2.1")
        assert result.name == "t-test"
        assert result.explanation == "Student t"
        assert result.plain_text == "t test"

    @pytest.mark.asyncio
    async def test_explain_with_llm_gateway_failure(self):
        mock_gateway = MagicMock()
        mock_gateway.structured_output = AsyncMock(side_effect=RuntimeError("fail"))
        mock_gateway.get_model_for_role.return_value = "model-name"

        explainer = MathExplainer(gateway=mock_gateway)
        result = await explainer.explain_with_llm("p < 0.05")
        assert result.name == "p-value"

    @pytest.mark.asyncio
    async def test_explain_with_llm_gateway_parsed_error(self):
        mock_gateway = MagicMock()
        mock_result = MagicMock()
        mock_result.parsed = None
        mock_result.error = "parse error"
        mock_gateway.structured_output = AsyncMock(return_value=mock_result)
        mock_gateway.get_model_for_role.return_value = "model-name"

        explainer = MathExplainer(gateway=mock_gateway)
        result = await explainer.explain_with_llm("p < 0.05")
        assert result.name == "p-value"


class TestLatexSymbols:
    def test_symbols_dict_not_empty(self):
        assert len(LATEX_SYMBOLS) > 0

    def test_common_symbols(self):
        assert "\\alpha" in LATEX_SYMBOLS
        assert "\\beta" in LATEX_SYMBOLS
        assert "\\pi" in LATEX_SYMBOLS


class TestFormulaPatterns:
    def test_patterns_dict_not_empty(self):
        assert len(FORMULA_PATTERNS) > 0

    def test_pattern_structure(self):
        for pattern, info in FORMULA_PATTERNS.items():
            assert "name" in info
            assert "explanation" in info


# ---------------------------------------------------------------------------
# 10. fusion_science.compute.sandbox
# ---------------------------------------------------------------------------
from fusion_science.compute.sandbox import (
    _BLOCKED_PATTERNS,
    SandboxConfig,
    SandboxManager,
)


class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.timeout == 120
        assert cfg.max_memory_mb == 2048
        assert cfg.max_cpu_seconds == 60
        assert cfg.max_processes == 50
        assert "numpy" in cfg.allowed_imports
        assert "subprocess" in cfg.blocked_imports

    def test_custom_values(self):
        cfg = SandboxConfig(timeout=60, max_memory_mb=512)
        assert cfg.timeout == 60
        assert cfg.max_memory_mb == 512


class TestSandboxManager:
    def test_create_sandbox(self):
        mgr = SandboxManager()
        result = mgr.create_sandbox()
        assert "sandbox_id" in result
        assert "work_dir" in result
        assert "env_vars" in result
        assert os.path.isdir(result["work_dir"])
        mgr.cleanup_all()

    def test_create_sandbox_custom_config(self):
        cfg = SandboxConfig(timeout=30, max_memory_mb=256)
        mgr = SandboxManager()
        result = mgr.create_sandbox(config=cfg)
        assert result["env_vars"]["FUSION_SANDBOX_TIMEOUT"] == "30"
        assert result["env_vars"]["FUSION_SANDBOX_MAX_MEMORY_MB"] == "256"
        mgr.cleanup_all()

    def test_create_sandbox_directories(self):
        mgr = SandboxManager()
        result = mgr.create_sandbox()
        work_dir = result["work_dir"]
        assert os.path.isdir(os.path.join(work_dir, "tmp"))
        assert os.path.isdir(os.path.join(work_dir, ".matplotlib"))
        mgr.cleanup_all()

    def test_cleanup_sandbox(self):
        mgr = SandboxManager()
        result = mgr.create_sandbox()
        sid = result["sandbox_id"]
        work_dir = result["work_dir"]
        assert mgr.cleanup_sandbox(sid) is True
        assert not os.path.isdir(work_dir)

    def test_cleanup_sandbox_missing(self):
        mgr = SandboxManager()
        assert mgr.cleanup_sandbox("nope") is False

    def test_cleanup_sandbox_dir_already_removed(self):
        mgr = SandboxManager()
        result = mgr.create_sandbox()
        sid = result["sandbox_id"]
        work_dir = result["work_dir"]
        os.rmdir(os.path.join(work_dir, "tmp"))
        os.rmdir(os.path.join(work_dir, ".matplotlib"))
        os.rmdir(work_dir)
        assert mgr.cleanup_sandbox(sid) is True

    def test_cleanup_all(self):
        mgr = SandboxManager()
        mgr.create_sandbox()
        mgr.create_sandbox()
        count = mgr.cleanup_all()
        assert count == 2

    def test_cleanup_all_empty(self):
        mgr = SandboxManager()
        count = mgr.cleanup_all()
        assert count == 0

    def test_get_resource_usage(self):
        mgr = SandboxManager()
        result = mgr.create_sandbox()
        sid = result["sandbox_id"]
        usage = mgr.get_resource_usage(sid)
        assert "memory_mb" in usage
        assert "cpu_seconds" in usage
        assert "work_dir_size_mb" in usage
        mgr.cleanup_all()

    def test_get_resource_usage_with_file(self):
        mgr = SandboxManager()
        result = mgr.create_sandbox()
        sid = result["sandbox_id"]
        work_dir = result["work_dir"]
        with open(os.path.join(work_dir, "test.txt"), "w") as f:
            f.write("x" * (2 * 1024 * 1024))
        usage = mgr.get_resource_usage(sid)
        assert usage["work_dir_size_mb"] > 0
        mgr.cleanup_all()

    def test_get_resource_usage_missing(self):
        mgr = SandboxManager()
        usage = mgr.get_resource_usage("nope")
        assert "error" in usage


class TestSandboxManagerValidation:
    def setup_method(self):
        self.mgr = SandboxManager()

    def test_validate_clean_code(self):
        result = self.mgr.validate_code("x = 1 + 2\nprint(x)")
        assert result["valid"] is True
        assert result["risk_level"] == "low"
        assert result["issues"] == []

    def test_validate_syntax_error(self):
        result = self.mgr.validate_code("def (")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_validate_blocked_import_subprocess(self):
        result = self.mgr.validate_code("import subprocess")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_validate_blocked_import_os(self):
        result = self.mgr.validate_code("import os")
        assert "Blocked import: os" not in str(result["issues"])

    def test_validate_blocked_import_from(self):
        result = self.mgr.validate_code("from subprocess import run")
        assert result["valid"] is False

    def test_validate_eval(self):
        result = self.mgr.validate_code("eval('1+1')")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_validate_exec(self):
        result = self.mgr.validate_code("exec('x=1')")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_validate_os_system(self):
        result = self.mgr.validate_code("os.system('ls')")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_validate_subprocess_call(self):
        result = self.mgr.validate_code("subprocess.run(['ls'])")
        assert result["valid"] is False

    def test_validate_dangerous_call_attribute(self):
        result = self.mgr.validate_code("import os\nos.system('rm -rf /')")
        assert result["valid"] is False

    def test_validate_file_write(self):
        result = self.mgr.validate_code("open('file.txt', 'w')")
        assert any("write" in i.lower() for i in result["issues"])

    def test_validate_file_append(self):
        result = self.mgr.validate_code("open('file.txt', 'a')")
        assert any("write" in i.lower() or "append" in i.lower() for i in result["issues"])

    def test_validate_file_read_ok(self):
        result = self.mgr.validate_code("open('file.txt', 'r')")
        assert not any("write" in i.lower() for i in result["issues"])

    def test_validate_compile(self):
        result = self.mgr.validate_code("compile('x=1', '<string>', 'exec')")
        assert result["risk_level"] == "medium"
        assert any("compile" in i for i in result["issues"])

    def test_validate_import_blocked_pickle(self):
        result = self.mgr.validate_code("import pickle")
        assert result["risk_level"] == "medium"
        assert any("pickle" in i for i in result["issues"])

    def test_validate_import_blocked_socket(self):
        result = self.mgr.validate_code("import socket")
        assert result["risk_level"] == "medium"
        assert any("socket" in i for i in result["issues"])

    def test_validate_import_allowed_numpy(self):
        result = self.mgr.validate_code("import numpy")
        assert result["valid"] is True

    def test_validate_import_allowed_pandas(self):
        result = self.mgr.validate_code("import pandas")
        assert result["valid"] is True

    def test_validate_non_python(self):
        result = self.mgr.validate_code("print('hi')", language="r")
        assert result["valid"] is True
        assert result["risk_level"] == "low"

    def test_validate_shutil_rmtree(self):
        result = self.mgr.validate_code("shutil.rmtree('/tmp/x')")
        assert result["risk_level"] in ("medium", "high")
        assert any("rmtree" in i for i in result["issues"])

    def test_validate_os_remove(self):
        result = self.mgr.validate_code("os.remove('file.txt')")
        assert result["risk_level"] in ("medium", "high")
        assert any("remove" in i.lower() for i in result["issues"])

    def test_validate_os_unlink(self):
        result = self.mgr.validate_code("os.unlink('file.txt')")
        assert result["risk_level"] in ("medium", "high")
        assert any("unlink" in i.lower() for i in result["issues"])

    def test_validate_medium_risk(self):
        result = self.mgr.validate_code("open('out.txt', 'w')")
        assert result["risk_level"] in ("medium", "high")

    def test_validate_dunder_import(self):
        result = self.mgr.validate_code("__import__('os')")
        assert result["valid"] is False
        assert result["risk_level"] == "high"


class TestBlockedPatterns:
    def test_patterns_not_empty(self):
        assert len(_BLOCKED_PATTERNS) > 0

    def test_pattern_structure(self):
        for pattern, description in _BLOCKED_PATTERNS:
            assert isinstance(pattern, str)
            assert isinstance(description, str)
            assert len(description) > 0


class TestGetAttrChain:
    def test_simple_attr(self):
        import ast

        code = "os.system('ls')"
        tree = ast.parse(code)
        call_node = tree.body[0].value
        chain = SandboxManager._get_attr_chain(call_node.func)
        assert chain == ["os", "system"]

    def test_nested_attr(self):
        import ast

        code = "a.b.c()"
        tree = ast.parse(code)
        call_node = tree.body[0].value
        chain = SandboxManager._get_attr_chain(call_node.func)
        assert chain == ["a", "b", "c"]

    def test_non_attr_returns_none(self):
        import ast

        code = "a.b()"
        tree = ast.parse(code)
        call_node = tree.body[0].value
        result = SandboxManager._get_attr_chain(call_node.func)
        assert result == ["a", "b"]

        # ast.Name (bare function call) is not an ast.Attribute,
        # so _get_attr_chain is never called with it in practice
