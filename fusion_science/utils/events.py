from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: float = 0.0


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history: int = 1000

    def on(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)
        logger.debug("EventBus: registered handler for '%s'", event_type)

    def off(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)
            logger.debug("EventBus: removed handler for '%s'", event_type)

    async def emit(self, event_type: str, data: dict[str, Any] | None = None, source: str = "") -> None:
        import time
        event = Event(
            type=event_type,
            data=data or {},
            source=source,
            timestamp=time.time(),
        )
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._handlers.get(event_type, [])
        if not handlers:
            logger.debug("EventBus: no handlers for '%s'", event_type)
            return

        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error("EventBus handler error for '%s': %s", event_type, e)

    def get_history(self, event_type: str | None = None, limit: int = 100) -> list[Event]:
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def clear_history(self) -> None:
        self._history.clear()

    def handler_count(self, event_type: str | None = None) -> int:
        if event_type:
            return len(self._handlers.get(event_type, []))
        return sum(len(v) for v in self._handlers.values())


# Global event bus instance
_global_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus


def reset_event_bus() -> None:
    global _global_bus
    _global_bus = None


# Event type constants
EVENT_DB_QUERY = "db_query"
EVENT_CODE_EXECUTION = "code_execution"
EVENT_LLM_CALL = "llm_call"
EVENT_VISUALIZATION = "visualization"
EVENT_SESSION_CREATED = "session_created"
EVENT_SESSION_UPDATED = "session_updated"
EVENT_TOOL_EXECUTED = "tool_executed"
EVENT_ERROR = "error"
