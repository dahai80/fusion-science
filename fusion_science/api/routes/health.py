from __future__ import annotations

import logging
import os
import shutil

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> dict:
    # F-O1: deepened liveness — service up is necessary but not sufficient for
    # an operator. Report the inference-engine dependency (MLX), disk headroom
    # for session/cache DBs, and whether the session store is reachable.
    deps: dict[str, dict] = {}
    overall = "ok"

    # MLX inference engine via the gateway's cached status probe (TTL-throttled
    # so /health cannot be turned into a per-call status storm).
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is not None:
        try:
            # F-O1: check_mlx_memory is a coroutine — must be awaited, otherwise
            # the unawaited coroutine object lands in the response body and
            # pydantic cannot serialize it (PydanticSerializationError).
            if hasattr(gateway, "check_mlx_memory"):
                mem = await gateway.check_mlx_memory()
                deps["mlx"] = {"status": "ok", "memory": mem}
            else:
                deps["mlx"] = {"status": "unknown"}
        except Exception as e:
            deps["mlx"] = {"status": "degraded", "error": str(e)[:200]}
            overall = "degraded"
    else:
        deps["mlx"] = {"status": "not_initialized"}
        overall = "degraded"

    # Disk headroom for the process working dir (sessions.db, cache, traces).
    try:
        usage = shutil.disk_usage(os.getcwd())
        free_gb = usage.free / (1024**3)
        deps["disk"] = {"status": "ok" if free_gb > 0.5 else "low", "free_gb": round(free_gb, 2)}
        if free_gb <= 0.5:
            overall = "degraded"
    except Exception as e:
        deps["disk"] = {"status": "unknown", "error": str(e)[:200]}

    # Session store reachability.
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is not None:
        try:
            deps["sessions"] = {"status": "ok", "count": mgr.count_sessions()}
        except Exception as e:
            deps["sessions"] = {"status": "degraded", "error": str(e)[:200]}
            overall = "degraded"
    else:
        deps["sessions"] = {"status": "not_initialized"}

    return {"status": overall, "service": "fusion-science", "dependencies": deps}
