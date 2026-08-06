from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..audit.tracker import TraceRecorder
from ..config import ScienceConfig, load_config
from ..core.agents import QueryRouterAgent
from ..core.context_manager import ContextManager
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
from .routes import (
    analysis,
    audit_route,
    chat,
    citations,
    compute,
    databases,
    health,
    math,
    models,
    pipelines,
    review,
    search,
    security,
    sessions,
    system,
    tools,
    visualize,
    visualize_ext,
)

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
    config: ScienceConfig = getattr(app.state, "config", None) or load_config()
    app.state.config = config
    app.state.gateway = LLMGateway(
        model=config.model_name,
        base_url=config.engine_base_url,
        api_key=config.engine_api_key,
        temperature=config.engine_temperature,
        max_tokens=config.engine_max_tokens,
        timeout=config.engine_timeout,
    )
    if config.model_reasoning:
        app.state.gateway.set_model_for_role("reasoning", config.model_reasoning)
    if config.model_summarization:
        app.state.gateway.set_model_for_role("summarization", config.model_summarization)
    if config.model_code:
        app.state.gateway.set_model_for_role("code", config.model_code)
    app.state.session_manager = SessionManager(store=MemorySessionStore())
    app.state.gateway.start_connection_monitor(interval=30.0)

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry, config=config)
    app.state.tool_registry = tool_registry
    logger.info("Tool registry ready: %d tools", len(tool_registry.list_tools()))

    router_agent = QueryRouterAgent(engine=app.state.gateway, tool_registry=tool_registry)
    app.state.router_agent = router_agent
    logger.info("QueryRouterAgent ready: %d agents", len(router_agent.list_agents()))

    context_manager = ContextManager(
        session_manager=app.state.session_manager,
        gateway=app.state.gateway,
        max_tokens=config.engine_max_tokens,
        model=config.model_name,
    )
    app.state.context_manager = context_manager

    recorder = TraceRecorder()
    recorder.start_session(metadata={"api": True})
    app.state.recorder = recorder
    _audit_handler._recorder = recorder

    bus = get_event_bus()
    for event_type in _OP_MAP:
        bus.on(event_type, _audit_handler)

    logger.info("Fusion-Science API started: model=%s", config.model_name)
    yield

    bus = get_event_bus()
    for event_type in _OP_MAP:
        bus.off(event_type, _audit_handler)

    gw = getattr(app.state, "gateway", None)
    if gw:
        gw.stop_connection_monitor()

    with suppress(Exception):
        recorder.end_session()
    logger.info("Fusion-Science API shutdown")


def create_app(config: ScienceConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="Fusion-Science API",
        description="Local AI scientific research workbench",
        version="1.0.2",
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
    app.include_router(chat.router, prefix="/api/v1/sessions/{session_id}", tags=["chat"])
    app.include_router(search.router, prefix="/api/v1/sessions/{session_id}", tags=["search"])
    app.include_router(analysis.router, prefix="/api/v1/sessions/{session_id}", tags=["analysis"])
    app.include_router(visualize.router, prefix="/api/v1/sessions/{session_id}", tags=["visualize"])
    app.include_router(review.router, prefix="/api/v1/sessions/{session_id}", tags=["review"])
    app.include_router(audit_route.router, prefix="/api/v1/sessions/{session_id}/audit", tags=["audit"])
    app.include_router(databases.router, prefix="/api/v1/databases", tags=["databases"])
    app.include_router(pipelines.router, prefix="/api/v1/pipelines", tags=["pipelines"])
    app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
    app.include_router(citations.router, prefix="/api/v1/citations", tags=["citations"])
    app.include_router(math.router, prefix="/api/v1/math", tags=["math"])
    app.include_router(visualize_ext.router, prefix="/api/v1/viz", tags=["visualization-ext"])
    app.include_router(compute.router, prefix="/api/v1/compute", tags=["compute"])
    app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
    app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])
    app.include_router(security.router, prefix="/api/v1/security", tags=["security"])

    return app


app = create_app()
