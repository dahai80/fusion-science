from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from ..utils.events import EVENT_SESSION_CREATED, EVENT_SESSION_UPDATED, EventBus, get_event_bus
from .models import Artifact, ResearchContext, ResearchSession
from .store import MemorySessionStore, SessionStore

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        store: SessionStore | None = None,
        event_bus: EventBus | None = None,
        max_messages: int = 0,
        max_bytes: int = 0,
    ):
        self._store = store or MemorySessionStore()
        self._bus = event_bus or get_event_bus()
        # Per-session safety bounds: 0 disables (tests). Prevents a single long
        # conversation from exhausting process memory / bloating the SQLite row.
        self._max_messages = max_messages
        self._max_bytes = max_bytes
        # L-5: per-session locks prevent lost-update races when concurrent
        # requests load→mutate→save the same session.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        # I-6: setdefault is atomic within the single-threaded asyncio loop, so
        # two coroutines creating the same session's lock concurrently cannot
        # each build a different lock (the prior read-then-write could).
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def _save(self, session: ResearchSession) -> bool:
        # P1: offload blocking store I/O off the event loop.
        return await asyncio.to_thread(self._store.save, session)

    async def save(self, session: ResearchSession) -> bool:
        # F-A9: public persistence API so routes do not reach into the private
        # _store (which bypasses lock/emit and couples callers to the store
        # backend). Delegates to the offloaded _save.
        return await self._save(session)

    async def _load(self, session_id: str) -> ResearchSession | None:
        return await asyncio.to_thread(self._store.load, session_id)

    async def _delete(self, session_id: str) -> bool:
        return await asyncio.to_thread(self._store.delete, session_id)

    def close(self) -> None:
        # P1: release the persistent SQLite connection / cache at lifespan
        # shutdown so the process does not leak a file handle across restarts.
        close_fn = getattr(self._store, "close", None)
        if callable(close_fn):
            close_fn()
            logger.info("SessionStore closed")

    def _enforce_bounds(self, session: ResearchSession) -> None:
        # Drop oldest messages until under both the count and byte caps. Trimming
        # the head keeps recent context intact; an overflow is logged loudly so a
        # runaway conversation is visible rather than silently truncated.
        if self._max_messages and len(session.messages) > self._max_messages:
            overflow = len(session.messages) - self._max_messages
            del session.messages[:overflow]
            logger.warning("session %s exceeded message cap, dropped %d oldest", session.id, overflow)
        if self._max_bytes:
            total = len(json.dumps(session.messages, ensure_ascii=False, default=str).encode("utf-8"))
            while total > self._max_bytes and len(session.messages) > 1:
                session.messages.pop(0)
                total = len(json.dumps(session.messages, ensure_ascii=False, default=str).encode("utf-8"))
                logger.warning("session %s exceeded byte cap, dropped oldest message", session.id)

    async def create_session(self, title: str = "", owner: str = "") -> ResearchSession:
        now = time.time()
        session = ResearchSession(
            id=str(uuid.uuid4()),
            title=title or f"Research {now:.0f}",
            owner=owner or "local",
            created_at=now,
            updated_at=now,
            messages=[],
            context=ResearchContext(),
            artifacts=[],
            trace_ids=[],
        )
        await self._save(session)
        await self._bus.emit(EVENT_SESSION_CREATED, {"session_id": session.id}, source="session")
        logger.info("Session created: %s (%s) owner=%s", session.id, session.title, session.owner)
        return session

    def get_session(self, session_id: str) -> ResearchSession | None:
        return self._store.load(session_id)

    def get_session_owned(self, session_id: str, owner: str) -> ResearchSession | None:
        session = self._store.load(session_id)
        if session is None:
            return None
        if session.owner and session.owner != owner:
            return None
        return session

    def list_sessions(self, owner: str | None = None, limit: int = 100, offset: int = 0) -> list[ResearchSession]:
        sessions = self._store.list_all()
        if owner is not None:
            sessions = [s for s in sessions if not s.owner or s.owner == owner]
        return sessions[offset : offset + limit]

    def count_sessions(self, owner: str | None = None) -> int:
        sessions = self._store.list_all()
        if owner is not None:
            sessions = [s for s in sessions if not s.owner or s.owner == owner]
        return len(sessions)

    async def delete_session(self, session_id: str) -> bool:
        result = await self._delete(session_id)
        self._locks.pop(session_id, None)
        if result:
            logger.info("Session deleted: %s", session_id)
        return result

    async def purge_subject(self, subject: str) -> list[str]:
        # G9 DSAR / right-to-erasure (GDPR Art.17, HIPAA accounting-of-disclosures
        # purge). Delete EVERY session owned by `subject` across the store, plus
        # its per-session audit trace file. Returns the list of purged session
        # ids so the privacy route can report what was removed. Never raises on
        # a missing session/trace — erasure must be idempotent.
        purged: list[str] = []
        for session in self._store.list_all():
            if session.owner == subject:
                sid = session.id
                ok = await self._delete(sid)
                self._locks.pop(sid, None)
                # Best-effort audit-trace file removal (the recorder owns the dir,
                # but the trace file is named <session_id>.json and safe to drop).
                if ok:
                    purged.append(sid)
                    logger.info("DSAR purge: deleted session %s for subject=%s", sid, subject)
        return purged

    async def add_message(self, session_id: str, role: str, content: str) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = await self._load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.messages.append({"role": role, "content": content})
            self._enforce_bounds(session)
            session.updated_at = time.time()
            await self._save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "add_message"}, source="session"
        )
        return session

    async def add_artifact(self, session_id: str, artifact: Artifact) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = await self._load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.artifacts.append(artifact)
            session.updated_at = time.time()
            await self._save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "add_artifact"}, source="session"
        )
        return session

    def get_messages(self, session_id: str) -> list[dict]:
        session = self._store.load(session_id)
        if not session:
            return []
        return list(session.messages)

    async def replace_messages(self, session_id: str, messages: list[dict]) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = await self._load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.messages = list(messages)
            session.updated_at = time.time()
            await self._save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "replace_messages"}, source="session"
        )
        logger.info("Replaced messages for session %s: %d messages", session_id, len(messages))
        return session

    async def atomic_compress(
        self,
        session_id: str,
        transform,
    ) -> list[dict] | None:
        # Load→transform→save under the per-session lock so a concurrent
        # add_message cannot interleave between the compress's read and its
        # replace (which would silently drop the in-flight message — a lost
        # update). transform is an async fn(messages)->messages and may await
        # an LLM call; asyncio.Lock permits that without blocking other sessions.
        async with self._lock_for(session_id):
            session = await self._load(session_id)
            if not session:
                logger.warning("Session not found for compress: %s", session_id)
                return None
            current = list(session.messages)
            new_messages = await transform(current)
            if new_messages is None or new_messages == current:
                return list(current)
            session.messages = list(new_messages)
            self._enforce_bounds(session)
            session.updated_at = time.time()
            await self._save(session)
        await self._bus.emit(EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "compress"}, source="session")
        logger.info("Compressed session %s: %d->%d msgs", session_id, len(current), len(new_messages))
        return list(new_messages)

    async def update_title(self, session_id: str, title: str) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = await self._load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.title = title
            session.updated_at = time.time()
            await self._save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "update_title"}, source="session"
        )
        return session
