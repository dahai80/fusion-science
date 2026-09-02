from __future__ import annotations

import json
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .models import ResearchSession

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000


class SessionStore(ABC):
    @abstractmethod
    def save(self, session: ResearchSession) -> None: ...
    @abstractmethod
    def load(self, session_id: str) -> ResearchSession | None: ...
    @abstractmethod
    def delete(self, session_id: str) -> bool: ...
    @abstractmethod
    def list_all(self) -> list[ResearchSession]: ...


class MemorySessionStore(SessionStore):
    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self._sessions: dict[str, ResearchSession] = {}
        self._times: dict[str, float] = {}
        self._max = max_sessions

    def save(self, session: ResearchSession) -> None:
        self._sessions[session.id] = session
        self._times[session.id] = time.time()
        self._evict()

    def load(self, session_id: str) -> ResearchSession | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._times.pop(session_id, None)
            return True
        return False

    def list_all(self) -> list[ResearchSession]:
        return list(self._sessions.values())

    def _evict(self) -> None:
        while len(self._sessions) > self._max:
            oldest = min(self._times, key=self._times.get)
            self._sessions.pop(oldest, None)
            self._times.pop(oldest, None)
            logger.info("Evicted session: %s", oldest)


class SQLiteSessionStore(SessionStore):
    def __init__(self, db_path: str = "~/.cache/fusion-science/sessions.db"):
        self._db_path = str(Path(db_path).expanduser())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT DEFAULT '',
                    owner TEXT DEFAULT '',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0,
                    version INTEGER DEFAULT 0,
                    messages TEXT DEFAULT '[]',
                    context TEXT DEFAULT '{}',
                    artifacts TEXT DEFAULT '[]',
                    trace_ids TEXT DEFAULT '[]'
                )
            """)
            # Migrate legacy schema: add columns if missing (idempotent)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "owner" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN owner TEXT DEFAULT ''")
            if "version" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN version INTEGER DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner)")

    def save(self, session: ResearchSession) -> bool:
        data = session.to_dict()
        with self._conn() as conn:
            if session.version > 0:
                cursor = conn.execute(
                    """
                    UPDATE sessions
                    SET title=?, owner=?, created_at=?, updated_at=?, version=version+1,
                        messages=?, context=?, artifacts=?, trace_ids=?
                    WHERE session_id=? AND version=?
                    """,
                    (
                        data["title"],
                        data["owner"],
                        data["created_at"],
                        data["updated_at"],
                        json.dumps(data["messages"], ensure_ascii=False),
                        json.dumps(data["context"], ensure_ascii=False),
                        json.dumps(data["artifacts"], ensure_ascii=False),
                        json.dumps(data["trace_ids"], ensure_ascii=False),
                        data["id"],
                        session.version,
                    ),
                )
                if cursor.rowcount == 0:
                    logger.warning("optimistic-lock conflict on session %s v%d", data["id"], session.version)
                    return False
                session.version += 1
                return True
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, title, owner, created_at, updated_at, version,
                     messages, context, artifacts, trace_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    title=excluded.title, owner=excluded.owner,
                    updated_at=excluded.updated_at, version=version+1,
                    messages=excluded.messages, context=excluded.context,
                    artifacts=excluded.artifacts, trace_ids=excluded.trace_ids
                """,
                (
                    data["id"],
                    data["title"],
                    data["owner"],
                    data["created_at"],
                    data["updated_at"],
                    0,
                    json.dumps(data["messages"], ensure_ascii=False),
                    json.dumps(data["context"], ensure_ascii=False),
                    json.dumps(data["artifacts"], ensure_ascii=False),
                    json.dumps(data["trace_ids"], ensure_ascii=False),
                ),
            )
            session.version += 1
            return True

    def load(self, session_id: str) -> ResearchSession | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id, title, owner, created_at, updated_at, version, messages, context, artifacts, trace_ids FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
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
                "messages": json.loads(row[6]),
                "context": json.loads(row[7]),
                "artifacts": json.loads(row[8]),
                "trace_ids": json.loads(row[9]),
            }
        )

    def delete(self, session_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    def list_all(self) -> list[ResearchSession]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT session_id, title, owner, created_at, updated_at, version, messages, context, artifacts, trace_ids FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            ResearchSession.from_dict(
                {
                    "id": r[0],
                    "title": r[1],
                    "owner": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                    "version": r[5],
                    "messages": json.loads(r[6]),
                    "context": json.loads(r[7]),
                    "artifacts": json.loads(r[8]),
                    "trace_ids": json.loads(r[9]),
                }
            )
            for r in rows
        ]
