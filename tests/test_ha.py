from __future__ import annotations

import json
import sys
import types

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.config import ScienceConfig
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.session.models import ResearchSession
from fusion_science.utils.events import reset_event_bus

# --- Fake psycopg so PostgresSessionStore is testable without a live DB ---
#
# A minimal in-memory stand-in: connect() returns a FakeConn whose cursor
# executes against a Python dict keyed by session_id. JSONB columns are stored
# as parsed Python objects so the store's isinstance checks exercise both
# branches (psycopg 3 returns parsed JSON for JSONB columns).


class FakeCursor:
    def __init__(self, store):
        self._store = store
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql_l = sql.strip().lower()
        if sql_l.startswith("create table") or sql_l.startswith("create index"):
            self.rowcount = 0
            return
        if sql_l.startswith("select 1"):
            self._rows = [(1,)]
            self.rowcount = 1
            return
        if "where session_id = %s" in sql_l and "select" in sql_l:
            sid = params[0]
            row = self._store.get(sid)
            self._rows = [row] if row else []
            self.rowcount = 1 if row else 0
            return
        if "order by updated_at" in sql_l and "select" in sql_l:
            rows = sorted(self._store.values(), key=lambda r: r[4], reverse=True)
            self._rows = rows
            self.rowcount = len(rows)
            return
        if sql_l.startswith("insert into sessions"):
            # cols: id(0) title(1) owner(2) created(3) updated(4) version(5)
            #       messages(6) context(7) artifacts(8) trace(9)
            sid = params[0]
            if sid in self._store and "on conflict" in sql_l:
                # upsert: store the new version from EXCLUDED.version (params[5])
                self._store[sid] = (
                    params[0],
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    params[9],
                )
            else:
                self._store[sid] = (
                    params[0],
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    params[9],
                )
            self.rowcount = 1
            return
        if sql_l.startswith("update sessions"):
            # SET title(0) owner(1) created(2) updated(3) version(4) msg(5)
            #     ctx(6) art(7) trace(8) WHERE id(9) AND version(10)
            sid = params[9]
            ver = params[10]
            row = self._store.get(sid)
            if row and row[5] == ver:
                self._store[sid] = (
                    params[0],
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    params[9],
                )
                self.rowcount = 1
            else:
                self.rowcount = 0
            return
        if sql_l.startswith("delete from sessions"):
            sid = params[0]
            if sid in self._store:
                del self._store[sid]
                self.rowcount = 1
            else:
                self.rowcount = 0
            return
        self.rowcount = 0

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, store):
        self._store = store
        self._closed = False

    def cursor(self):
        return FakeCursor(self._store)

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_psycopg(monkeypatch):
    store: dict = {}

    fake = types.ModuleType("psycopg")

    def connect(dsn, autocommit=False):
        return FakeConn(store)

    fake.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    return store


@pytest.fixture
def pg_store(monkeypatch):
    _install_fake_psycopg(monkeypatch)
    from fusion_science.session.postgres_store import PostgresSessionStore

    return PostgresSessionStore(dsn="postgresql://u:p@localhost/fs")


class TestPostgresSessionStore:
    def test_missing_dsn_raises(self, monkeypatch):
        _install_fake_psycopg(monkeypatch)
        from fusion_science.session.postgres_store import PostgresSessionStore

        with pytest.raises(ValueError):
            PostgresSessionStore(dsn="")

    def test_missing_psycopg_raises(self, monkeypatch):
        # ensure no real psycopg interferes
        monkeypatch.setitem(sys.modules, "psycopg", None)
        from fusion_science.session.postgres_store import PostgresSessionStore

        with pytest.raises(RuntimeError):
            PostgresSessionStore(dsn="postgresql://u:p@localhost/fs")

    def test_save_and_load_roundtrip(self, pg_store):
        s = ResearchSession(id="s1", title="t", owner="alice", messages=[{"role": "user", "content": "hi"}])
        assert pg_store.save(s) is True
        assert s.version == 1
        loaded = pg_store.load("s1")
        assert loaded is not None
        assert loaded.id == "s1"
        assert loaded.owner == "alice"
        assert loaded.messages == [{"role": "user", "content": "hi"}]

    def test_load_missing(self, pg_store):
        assert pg_store.load("nope") is None

    def test_optimistic_lock_conflict(self, pg_store):
        s = ResearchSession(id="s2", title="t", owner="bob")
        assert pg_store.save(s) is True  # INSERT, version 0 → 1
        assert pg_store.save(s) is True  # UPDATE v1→2, now version 2
        # stale copy: in-memory version 1, but the row is already at 2
        s.version = 1
        assert pg_store.save(s) is False  # conflict, no update

    def test_delete(self, pg_store):
        s = ResearchSession(id="s3", title="t")
        pg_store.save(s)
        assert pg_store.delete("s3") is True
        assert pg_store.delete("s3") is False

    def test_list_all(self, pg_store):
        pg_store.save(ResearchSession(id="a", title="a"))
        pg_store.save(ResearchSession(id="b", title="b"))
        ids = {s.id for s in pg_store.list_all()}
        assert {"a", "b"} <= ids

    def test_ping_ok(self, pg_store):
        assert pg_store.ping() is True


@pytest.fixture
async def ha_app(monkeypatch):
    reset_event_bus()
    _install_fake_psycopg(monkeypatch)
    monkeypatch.setenv("FUSION_SCIENCE_SESSION_STORE", "postgres")
    monkeypatch.setenv("FUSION_SCIENCE_SESSION_DSN", "postgresql://u:p@localhost/fs")
    monkeypatch.setenv("FUSION_SCIENCE_API_KEYS", "admin:admin-key")
    monkeypatch.setenv("FUSION_SCIENCE_JWT_SECRET", "test-secret")
    config = ScienceConfig()
    config.session_store = "postgres"
    config.session_dsn = "postgresql://u:p@localhost/fs"
    application = create_app(config=config)
    application.state.config = config
    from fusion_science.core.gateway import LLMGateway
    from fusion_science.session.postgres_store import PostgresSessionStore

    application.state.gateway = LLMGateway(config)
    # ASGITransport does not run FastAPI lifespan, so wire the shared Postgres
    # store + manager the way app.py lifespan would.
    application.state.session_manager = SessionManager(PostgresSessionStore(dsn=config.session_dsn))
    yield application
    reset_event_bus()


class TestReadiness:
    @pytest.mark.asyncio
    async def test_ready_200_with_memory_store(self):
        # default app fixture path: memory store has no ping → always ok
        reset_event_bus()
        config = ScienceConfig()
        config.session_store = "memory"
        app = create_app(config=config)
        app.state.config = config
        from fusion_science.core.gateway import LLMGateway

        app.state.gateway = LLMGateway(config)
        app.state.session_manager = SessionManager(MemorySessionStore())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/ready")
            assert resp.status_code == 200
            assert resp.json()["ready"] is True

    @pytest.mark.asyncio
    async def test_ready_503_when_store_down(self, ha_app):
        # force the store ping to fail
        store = ha_app.state.session_manager._store
        store.ping = lambda: False
        transport = ASGITransport(app=ha_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/ready")
            assert resp.status_code == 503
            body = resp.json()
            assert body["ready"] is False
            assert body["checks"]["session_store"]["status"] == "down"

    @pytest.mark.asyncio
    async def test_ready_200_when_store_up(self, ha_app):
        transport = ASGITransport(app=ha_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/ready")
            assert resp.status_code == 200
            assert resp.json()["checks"]["session_store"]["status"] == "ok"


class TestAuditSink:
    def test_sink_forwards_entry(self, monkeypatch):
        # F-ENT-HA-SINK: record() with a sink_url fires a daemon-thread POST.
        # We intercept httpx.post to capture the NDJSON line without network.
        import fusion_science.audit.tracker as tr_mod

        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

        def fake_post(url, content=None, headers=None, timeout=None):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            return FakeResp()

        # httpx is imported lazily inside _post; patch the real module
        import httpx

        monkeypatch.setattr(httpx, "post", fake_post)
        recorder = tr_mod.TraceRecorder(sink_url="https://siem.example/ingest")
        recorder.start_session()
        recorder.record("llm_call", "test", "probe", result_summary="ok")
        # the post runs on a daemon thread; give it a moment
        import time

        for _ in range(50):
            if "content" in captured:
                break
            time.sleep(0.01)
        assert captured.get("url") == "https://siem.example/ingest"
        assert captured["content"].endswith("\n")
        line = json.loads(captured["content"].strip())
        assert line["operation"] == "llm_call"

    def test_no_sink_no_forward(self, monkeypatch):
        import fusion_science.audit.tracker as tr_mod

        called = {"n": 0}
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        recorder = tr_mod.TraceRecorder(sink_url="")
        recorder.start_session()
        recorder.record("llm_call", "test", "probe")
        import time

        time.sleep(0.05)
        assert called["n"] == 0
