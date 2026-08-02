from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..audit.tracker import TraceRecorder
from ..config import ScienceConfig
from ..core.agents import QueryRouterAgent
from ..core.gateway import LLMGateway
from ..core.tools import ToolRegistry, register_builtin_tools
from ..session import MemorySessionStore, SessionManager
from ..utils.events import (
    EVENT_CODE_EXECUTION,
    EVENT_DB_QUERY,
    EVENT_ERROR,
    EVENT_LLM_CALL,
    EVENT_TOOL_EXECUTED,
    EVENT_VISUALIZATION,
    get_event_bus,
)
from .middleware import APIKeyMiddleware
from .routes import analysis, audit_route, chat, health, review, search, sessions, visualize

logger = logging.getLogger(__name__)

_OP_MAP = {
    EVENT_DB_QUERY: "db_query",
    EVENT_CODE_EXECUTION: "code_execution",
    EVENT_LLM_CALL: "llm_call",
    EVENT_VISUALIZATION: "visualization",
    EVENT_TOOL_EXECUTED: "tool_executed",
    EVENT_ERROR: "error",
}


async def _audit_handler(event):
    recorder: TraceRecorder | None = getattr(_audit_handler, "_recorder", None)
    if recorder is None:
        return
    op = _OP_MAP.get(event.type, event.type)
    recorder.record(
        operation=op,
        source=event.source,
        description=f"{event.type}: {event.data.get('session_id', '')}",
        parameters=event.data,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: ScienceConfig = getattr(app.state, "config", None) or ScienceConfig.from_env()
    app.state.config = config
    app.state.gateway = LLMGateway(config)
    app.state.session_manager = SessionManager(store=MemorySessionStore())

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry, config)
    app.state.tool_registry = tool_registry

    app.state.router_agent = QueryRouterAgent(
        engine=app.state.gateway,
        tool_registry=tool_registry,
    )

    recorder = TraceRecorder()
    recorder.start_session(metadata={"api": True})
    app.state.recorder = recorder
    _audit_handler._recorder = recorder

    bus = get_event_bus()
    for event_type in _OP_MAP:
        bus.on(event_type, _audit_handler)

    logger.info("Fusion-Science API started: model=%s, tools=%d", config.model, len(tool_registry.list_tools()))
    yield

    bus = get_event_bus()
    for event_type in _OP_MAP:
        bus.off(event_type, _audit_handler)

    with suppress(Exception):
        recorder.end_session()
    logger.info("Fusion-Science API shutdown")


def create_app(config: ScienceConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="Fusion-Science API",
        description="Local AI scientific research workbench",
        version="0.4.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(APIKeyMiddleware)

    if config:
        app.state.config = config

    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(search.router, prefix="/api/v1/search", tags=["search"])
    app.include_router(analysis.router, prefix="/api/v1/analyze", tags=["analysis"])
    app.include_router(visualize.router, prefix="/api/v1/visualize", tags=["visualize"])
    app.include_router(review.router, prefix="/api/v1/review", tags=["review"])
    app.include_router(audit_route.router, prefix="/api/v1/sessions/{session_id}/audit", tags=["audit"])

    from ..mcp_server import router as mcp_router
    app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])

    return app


app = create_app()
