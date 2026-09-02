from __future__ import annotations

import asyncio
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
    ):
        self._store = store or MemorySessionStore()
        self._bus = event_bus or get_event_bus()
        # L-5: per-session locks prevent lost-update races when concurrent
        # requests load→mutate→save the same session.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def create_session(self, title: str = "") -> ResearchSession:
        now = time.time()
        session = ResearchSession(
            id=str(uuid.uuid4()),
            title=title or f"Research {now:.0f}",
            created_at=now,
            updated_at=now,
            messages=[],
            context=ResearchContext(),
            artifacts=[],
            trace_ids=[],
        )
        self._store.save(session)
        await self._bus.emit(EVENT_SESSION_CREATED, {"session_id": session.id}, source="session")
        logger.info("Session created: %s (%s)", session.id, session.title)
        return session

    def get_session(self, session_id: str) -> ResearchSession | None:
        return self._store.load(session_id)

    def list_sessions(self) -> list[ResearchSession]:
        return self._store.list_all()

    async def delete_session(self, session_id: str) -> bool:
        result = self._store.delete(session_id)
        self._locks.pop(session_id, None)
        if result:
            logger.info("Session deleted: %s", session_id)
        return result

    async def add_message(self, session_id: str, role: str, content: str) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = self._store.load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.messages.append({"role": role, "content": content})
            session.updated_at = time.time()
            self._store.save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "add_message"}, source="session"
        )
        return session

    async def add_artifact(self, session_id: str, artifact: Artifact) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = self._store.load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.artifacts.append(artifact)
            session.updated_at = time.time()
            self._store.save(session)
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
            session = self._store.load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.messages = list(messages)
            session.updated_at = time.time()
            self._store.save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "replace_messages"}, source="session"
        )
        logger.info("Replaced messages for session %s: %d messages", session_id, len(messages))
        return session

    async def update_title(self, session_id: str, title: str) -> ResearchSession | None:
        async with self._lock_for(session_id):
            session = self._store.load(session_id)
            if not session:
                logger.warning("Session not found: %s", session_id)
                return None
            session.title = title
            session.updated_at = time.time()
            self._store.save(session)
        await self._bus.emit(
            EVENT_SESSION_UPDATED, {"session_id": session_id, "action": "update_title"}, source="session"
        )
        return session
