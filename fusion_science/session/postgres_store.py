from __future__ import annotations

import json
import logging

from .models import ResearchSession
from .store import SessionStore

logger = logging.getLogger(__name__)


class PostgresSessionStore(SessionStore):
    # F-ENT-HA: shared session store for multi-node HA (issue #24). Each API
    # worker pod connects to the same Postgres instance, so a request routed
    # to any node sees the same sessions — no sticky sessions, no in-memory
    # state to lose on failover. Reuses ResearchSession.to_dict/from_dict for
    # serialization (same JSON contract as SQLiteSessionStore) so a deployment
    # can migrate sqlite → postgres without changing the session shape.
    #
    # psycopg is an OPTIONAL dependency (the [ha] extra). Lazy import so a
    # default install with no Postgres backend is unaffected. Connection is
    # pool-managed per store instance; optimistic locking via the version
    # column prevents lost updates when two nodes edit the same session.

    def __init__(self, dsn: str = "", min_conn: int = 1, max_conn: int = 8):
        if not dsn:
            raise ValueError("PostgresSessionStore requires a DSN (FUSION_SCIENCE_SESSION_DSN)")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgresSessionStore needs psycopg (install fusion-science[ha])") from exc
        self._dsn = dsn
        # Simple connection pool: a thread-safe queue of reusable connections.
        # psycopg 3 connections are not shareable across concurrent coroutines,
        # so SessionManager's per-session asyncio lock serializes access; the
        # pool just avoids reconnect churn under many sessions.
        import queue

        self._pool: queue.Queue = queue.Queue(maxsize=max_conn)
        for _ in range(min_conn):
            self._pool.put(psycopg.connect(dsn, autocommit=False))
        self._psycopg = psycopg
        self._max_conn = max_conn
        self._init_db()

    def _conn(self):
        return self._pool.get(timeout=30.0)

    def _put(self, conn):
        try:
            self._pool.put_nowait(conn)
        except Exception:
            with __import__("contextlib").suppress(Exception):
                conn.close()

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                        CREATE TABLE IF NOT EXISTS sessions (
                            session_id  TEXT PRIMARY KEY,
                            title       TEXT DEFAULT '',
                            owner       TEXT DEFAULT '',
                            created_at  DOUBLE PRECISION DEFAULT 0,
                            updated_at  DOUBLE PRECISION DEFAULT 0,
                            version     INTEGER DEFAULT 0,
                            messages    JSONB DEFAULT '[]',
                            context     JSONB DEFAULT '{}',
                            artifacts   JSONB DEFAULT '[]',
                            trace_ids   JSONB DEFAULT '[]'
                        )
                        """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")
        finally:
            self._put(conn)
        logger.info("PostgresSessionStore initialized (dsn truncated): %s", self._dsn.split("@")[-1])

    def save(self, session: ResearchSession) -> bool:
        data = session.to_dict()
        # F-ENT-LOCK: row version must match in-memory version to update; on
        # success both advance to the same new version (see SQLiteSessionStore).
        new_version = session.version + 1
        conn = self._conn()
        try:
            with conn, conn.cursor() as cur:
                if session.version > 0:
                    cur.execute(
                        """
                            UPDATE sessions
                            SET title=%s, owner=%s, created_at=%s, updated_at=%s,
                                version=%s, messages=%s, context=%s,
                                artifacts=%s, trace_ids=%s
                            WHERE session_id=%s AND version=%s
                            """,
                        (
                            data["title"],
                            data["owner"],
                            data["created_at"],
                            data["updated_at"],
                            new_version,
                            json.dumps(data["messages"], ensure_ascii=False),
                            json.dumps(data["context"], ensure_ascii=False),
                            json.dumps(data["artifacts"], ensure_ascii=False),
                            json.dumps(data["trace_ids"], ensure_ascii=False),
                            data["id"],
                            session.version,
                        ),
                    )
                    if cur.rowcount == 0:
                        logger.warning("optimistic-lock conflict on session %s v%d", data["id"], session.version)
                        return False
                    session.version = new_version
                    return True
                cur.execute(
                    """
                        INSERT INTO sessions
                            (session_id, title, owner, created_at, updated_at, version,
                             messages, context, artifacts, trace_ids)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(session_id) DO UPDATE SET
                            title=EXCLUDED.title, owner=EXCLUDED.owner,
                            updated_at=EXCLUDED.updated_at, version=EXCLUDED.version,
                            messages=EXCLUDED.messages, context=EXCLUDED.context,
                            artifacts=EXCLUDED.artifacts, trace_ids=EXCLUDED.trace_ids
                        """,
                    (
                        data["id"],
                        data["title"],
                        data["owner"],
                        data["created_at"],
                        data["updated_at"],
                        new_version,
                        json.dumps(data["messages"], ensure_ascii=False),
                        json.dumps(data["context"], ensure_ascii=False),
                        json.dumps(data["artifacts"], ensure_ascii=False),
                        json.dumps(data["trace_ids"], ensure_ascii=False),
                    ),
                )
            session.version = new_version
            return True
        finally:
            self._put(conn)

    def load(self, session_id: str) -> ResearchSession | None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_id, title, owner, created_at, updated_at, version, "
                    "messages, context, artifacts, trace_ids FROM sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
            if not row:
                return None
            return ResearchSession.from_dict(
                {
                    "id": row[0],
                    "title": row[1],
                    "owner": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                    "version": row[5],
                    "messages": row[6] if isinstance(row[6], list) else json.loads(row[6]),
                    "context": row[7] if isinstance(row[7], dict) else json.loads(row[7]),
                    "artifacts": row[8] if isinstance(row[8], list) else json.loads(row[8]),
                    "trace_ids": row[9] if isinstance(row[9], list) else json.loads(row[9]),
                }
            )
        finally:
            self._put(conn)

    def delete(self, session_id: str) -> bool:
        conn = self._conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
                return cur.rowcount > 0
        finally:
            self._put(conn)

    def list_all(self) -> list[ResearchSession]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_id, title, owner, created_at, updated_at, version, "
                    "messages, context, artifacts, trace_ids FROM sessions ORDER BY updated_at DESC"
                )
                rows = cur.fetchall()
            return [
                ResearchSession.from_dict(
                    {
                        "id": r[0],
                        "title": r[1],
                        "owner": r[2],
                        "created_at": r[3],
                        "updated_at": r[4],
                        "version": r[5],
                        "messages": r[6] if isinstance(r[6], list) else json.loads(r[6]),
                        "context": r[7] if isinstance(r[7], dict) else json.loads(r[7]),
                        "artifacts": r[8] if isinstance(r[8], list) else json.loads(r[8]),
                        "trace_ids": r[9] if isinstance(r[9], list) else json.loads(r[9]),
                    }
                )
                for r in rows
            ]
        finally:
            self._put(conn)

    def close(self) -> None:
        import contextlib

        while not self._pool.empty():
            conn = self._pool.get_nowait()
            with contextlib.suppress(Exception):
                conn.close()

    def ping(self) -> bool:
        # HA readiness probe: a live DB round-trip is the dependency health
        # signal for /api/v1/ready. Returns False (not raise) on failure so
        # the readiness endpoint can report degraded without 500-ing.
        try:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return cur.fetchone() is not None
            finally:
                self._put(conn)
        except Exception as e:
            logger.warning("PostgresSessionStore ping failed: %s", e)
            return False
