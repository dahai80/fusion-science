from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..audit.tracker import TraceRecorder
from ..config import ScienceConfig, load_config
from ..core.agents import QueryRouterAgent
from ..core.context_manager import ContextManager
from ..core.gateway import LLMGateway
from ..core.tools import ToolRegistry, register_builtin_tools
from ..mcp_server import router as mcp_router
from ..session import MemorySessionStore, SessionManager, SQLiteSessionStore
from ..utils.events import (
    EVENT_CODE_EXECUTION,
    EVENT_DB_QUERY,
    EVENT_ERROR,
    EVENT_LLM_CALL,
    EVENT_TOOL_EXECUTED,
    EVENT_VISUALIZATION,
    get_event_bus,
)
from .middleware import APIKeyMiddleware, MetricsMiddleware, RateLimitMiddleware
from .routes import (
    analysis,
    audit_route,
    auth_route,
    chat,
    citations,
    compute,
    databases,
    health,
    math,
    metrics,
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
    # F-A1: multi-worker note. With the SQLite session store (default), sessions
    # persist across workers via the shared DB (WAL mode) so multi-worker is
    # safe for session continuity. EventBus/ScienceCache/MirrorRouter remain
    # process-local (per-worker), which is fine for read-mostly state. We warn
    # (not error) so a multi-worker deploy is informed, not blocked.
    _workers_env = (
        os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS") or os.getenv("FUSION_SCIENCE_WORKERS") or ""
    )
    if _workers_env.isdigit() and int(_workers_env) > 1:
        logger.warning(
            "FUSION-SCIENCE multi-worker: %s workers. Sessions are shared via the "
            "SQLite store (safe). EventBus/ScienceCache/MirrorRouter are per-worker "
            "(process-local) — acceptable for read-mostly state. Use --workers 1 for "
            "strictly-single-process semantics.",
            _workers_env,
        )

    # F-O4: optional rotating file logging for daemon deployments.
    from ..utils.logging_setup import configure_file_logging

    configure_file_logging()

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
    # Production uses SQLiteSessionStore (crash-safe persistence); "memory" is a
    # test-only override. Default caps bound per-session memory so a long
    # conversation cannot OOM the process.
    if config.session_store.lower() == "sqlite":
        store = SQLiteSessionStore(db_path=config.session_db_path)
        logger.info("Session store: sqlite (%s)", config.session_db_path)
    else:
        store = MemorySessionStore()
        logger.warning("Session store: memory — sessions lost on restart (test override)")
    app.state.session_manager = SessionManager(
        store=store,
        max_messages=config.session_max_messages,
        max_bytes=config.session_max_bytes,
    )
    app.state.gateway.start_connection_monitor(interval=30.0)

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry, config=config, gateway=app.state.gateway)
    app.state.tool_registry = tool_registry
    logger.info("Tool registry ready: %d tools", len(tool_registry.list_tools()))

    router_agent = QueryRouterAgent(engine=app.state.gateway, tool_registry=tool_registry)
    app.state.router_agent = router_agent
    logger.info("QueryRouterAgent ready: %d agents", len(router_agent.list_agents()))

    # F-A7: shared DatabaseAggregator so connector HTTP clients are reused
    # across searches instead of created/leaked per request. Closed on shutdown
    # to release httpx connection pools.
    from ..database.aggregator import DatabaseAggregator

    app.state.aggregator = DatabaseAggregator()

    context_manager = ContextManager(
        session_manager=app.state.session_manager,
        gateway=app.state.gateway,
        max_tokens=config.engine_max_tokens,
        model=config.model_name,
    )
    app.state.context_manager = context_manager

    # F-ENT-AUDIT: retention policy from env (days / max sessions). Defaults
    # keep 90 days / 1000 sessions so an unattended deploy does not fill disk.
    _audit_age = int(os.getenv("FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS", "90"))
    _audit_max = int(os.getenv("FUSION_SCIENCE_AUDIT_MAX_SESSIONS", "1000"))
    recorder = TraceRecorder(max_age_days=_audit_age, max_sessions=_audit_max)
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
            await gw.close()

    with suppress(Exception):
        recorder.end_session()

    # F-A2: close persistent store/cache/router handles so WAL files and FDs
    # do not accumulate across `start.sh restart` cycles.
    sm = getattr(app.state, "session_manager", None)
    if sm:
        # F-O6: snapshot the SQLite session DB before closing so a crash mid-run
        # (or WAL corruption) does not lose all research sessions. No-op for the
        # in-memory test store.
        store = getattr(sm, "_store", None)
        if hasattr(store, "backup"):
            with suppress(Exception):
                store.backup()
        with suppress(Exception):
            sm.close()
    with suppress(Exception):
        from ..database.mirror import reset_shared_cache, reset_shared_router

        reset_shared_cache()
        reset_shared_router()
    # F-A7: close shared aggregator's connector connection pools.
    agg = getattr(app.state, "aggregator", None)
    if agg:
        with suppress(Exception):
            await agg.close_all()
    logger.info("Fusion-Science API shutdown")


def create_app(config: ScienceConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="Fusion-Science API",
        description="Local AI scientific research workbench",
        version=__version__,
        lifespan=lifespan,
    )

    cors_config = config or ScienceConfig()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_config.api_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # F-O2: request/latency/error counters feed /metrics. Added outermost so
    # it counts every request including auth/rate-limit rejections.
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(APIKeyMiddleware)
    # F-S8: per-IP rate limit. Disabled by default (0); enable via
    # FUSION_SCIENCE_RATE_LIMIT (requests/window) and FUSION_SCIENCE_RATE_WINDOW.
    _rl_limit = int(os.getenv("FUSION_SCIENCE_RATE_LIMIT", "0"))
    _rl_window = int(os.getenv("FUSION_SCIENCE_RATE_WINDOW", "60"))
    if _rl_limit > 0:
        app.add_middleware(RateLimitMiddleware, limit=_rl_limit, window=_rl_window)
        logger.info("Rate limit enabled: %d req/%ds per IP", _rl_limit, _rl_window)

    if config:
        app.state.config = config

    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(auth_route.router, prefix="/api/v1", tags=["auth"])
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
    app.include_router(metrics.router, prefix="/api/v1", tags=["metrics"])
    app.include_router(visualize_ext.router, prefix="/api/v1/viz", tags=["visualization-ext"])
    app.include_router(compute.router, prefix="/api/v1/compute", tags=["compute"])
    app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
    app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])
    app.include_router(security.router, prefix="/api/v1/security", tags=["security"])

    # F-S11: MCP JSON-RPC 2.0 endpoint (initialize/tools.list/tools.call) + SSE
    # transport. Was previously declared but never mounted — dead code. Wired
    # here at /mcp so the MCP clients the README documents actually resolve.
    app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])

    return app


app = create_app()
