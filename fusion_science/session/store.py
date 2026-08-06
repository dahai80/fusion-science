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

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT DEFAULT '',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0,
                    messages TEXT DEFAULT '[]',
                    context TEXT DEFAULT '{}',
                    artifacts TEXT DEFAULT '[]',
                    trace_ids TEXT DEFAULT '[]'
                )
            """)

    def save(self, session: ResearchSession) -> None:
        data = session.to_dict()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (session_id, title, created_at, updated_at, messages, context, artifacts, trace_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["id"],
                    data["title"],
                    data["created_at"],
                    data["updated_at"],
                    json.dumps(data["messages"], ensure_ascii=False),
                    json.dumps(data["context"], ensure_ascii=False),
                    json.dumps(data["artifacts"], ensure_ascii=False),
                    json.dumps(data["trace_ids"], ensure_ascii=False),
                ),
            )

    def load(self, session_id: str) -> ResearchSession | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT session_id, title, created_at, updated_at, messages, context, artifacts, trace_ids FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return ResearchSession.from_dict(
            {
                "id": row[0],
                "title": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "messages": json.loads(row[4]),
                "context": json.loads(row[5]),
                "artifacts": json.loads(row[6]),
                "trace_ids": json.loads(row[7]),
            }
        )

    def delete(self, session_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    def list_all(self) -> list[ResearchSession]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT session_id, title, created_at, updated_at, messages, context, artifacts, trace_ids FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            ResearchSession.from_dict(
                {
                    "id": r[0],
                    "title": r[1],
                    "created_at": r[2],
                    "updated_at": r[3],
                    "messages": json.loads(r[4]),
                    "context": json.loads(r[5]),
                    "artifacts": json.loads(r[6]),
                    "trace_ids": json.loads(r[7]),
                }
            )
            for r in rows
        ]
