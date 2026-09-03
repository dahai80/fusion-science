from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from collections import OrderedDict
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
    # F-P7: O(1) LRU via OrderedDict. Insertion/access order tracks recency;
    # eviction pops the front (oldest) instead of a linear min() scan that
    # walked the whole session map on every save.
    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self._sessions: OrderedDict[str, ResearchSession] = OrderedDict()
        self._max = max_sessions

    def save(self, session: ResearchSession) -> None:
        if session.id in self._sessions:
            self._sessions.move_to_end(session.id)
        self._sessions[session.id] = session
        self._evict()

    def load(self, session_id: str) -> ResearchSession | None:
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions.move_to_end(session_id)
        return session

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_all(self) -> list[ResearchSession]:
        return list(self._sessions.values())

    def _evict(self) -> None:
        while len(self._sessions) > self._max:
            oldest_id, _ = self._sessions.popitem(last=False)
            logger.info("Evicted session: %s", oldest_id)


class SQLiteSessionStore(SessionStore):
    def __init__(self, db_path: str = "~/.cache/fusion-science/sessions.db"):
        self._db_path = str(Path(db_path).expanduser())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # P6: one persistent connection per store instance (check_same_thread
        # off — guarded by SessionManager's per-session asyncio locks + the
        # GIL) replaces a connect+PRAGMA round-trip on every save/load/delete.
        # WAL/busy_timeout set once at construction, not per call.
        self._conn = sqlite3.connect(self._db_path, timeout=5.0, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute("""
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
            cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
            if "owner" not in cols:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN owner TEXT DEFAULT ''")
            if "version" not in cols:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN version INTEGER DEFAULT 0")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner)")

    def save(self, session: ResearchSession) -> bool:
        data = session.to_dict()
        # F-ENT-LOCK: optimistic locking. The row's version must match the
        # in-memory version for an update to apply; on success BOTH the row
        # and the in-memory copy advance to the same new version, so the next
        # save's WHERE version=? matches. Pre-fix this stored 0 on INSERT while
        # bumping memory to 1 — the next UPDATE never matched and every second
        # save silently "conflicted". Now the stored version equals the
        # post-increment memory version on every path.
        new_version = session.version + 1
        with self._conn:
            if session.version > 0:
                cursor = self._conn.execute(
                    """
                    UPDATE sessions
                    SET title=?, owner=?, created_at=?, updated_at=?, version=?,
                        messages=?, context=?, artifacts=?, trace_ids=?
                    WHERE session_id=? AND version=?
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
                if cursor.rowcount == 0:
                    logger.warning("optimistic-lock conflict on session %s v%d", data["id"], session.version)
                    return False
                session.version = new_version
                return True
            self._conn.execute(
                """
                INSERT INTO sessions
                    (session_id, title, owner, created_at, updated_at, version,
                     messages, context, artifacts, trace_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    title=excluded.title, owner=excluded.owner,
                    updated_at=excluded.updated_at, version=excluded.version,
                    messages=excluded.messages, context=excluded.context,
                    artifacts=excluded.artifacts, trace_ids=excluded.trace_ids
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

    def load(self, session_id: str) -> ResearchSession | None:
        conn = self._conn
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
        with self._conn:
            cursor = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    def list_all(self) -> list[ResearchSession]:
        conn = self._conn
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

    def close(self) -> None:
        # P6: release the persistent connection on shutdown.
        with contextlib.suppress(Exception):
            self._conn.close()

    def backup(self, suffix: str = "") -> str | None:
        # F-O6: crash-safe backup of the live DB to a sibling .bak file using
        # SQLite's online backup API (consistent snapshot without blocking
        # writes). Rotates the previous backup to .bak.1 so the last two
        # backups survive a corruption that also damages the primary.
        import shutil

        try:
            base = self._db_path
            bak = f"{base}.bak{suffix}"
            bak_prev = f"{base}.bak{suffix}.1"
            if Path(bak_prev).exists():
                with contextlib.suppress(Exception):
                    shutil.move(bak, bak_prev) if Path(bak).exists() else None
            with sqlite3.connect(bak) as dest:
                self._conn.backup(dest)
            logger.info("Session DB backed up to %s", bak)
            return bak
        except Exception as e:
            logger.warning("Session DB backup failed: %s", e)
            return None
