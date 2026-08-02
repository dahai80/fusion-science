from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ...utils.offline import get_connectivity, is_offline

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status")
async def system_status(request: Request):
    config = getattr(request.app.state, "config", None)
    gateway = getattr(request.app.state, "gateway", None)
    offline = is_offline()
    result = {
        "offline": offline,
        "model": gateway.model if gateway else "unknown",
        "model_roles": gateway.get_model_roles() if gateway else {},
        "config_loaded": config is not None,
    }
    if gateway:
        stats = gateway.get_connection_stats()
        result["connection"] = {
            "state": stats.connection_state,
            "total_attempts": stats.total_attempts,
            "successful": stats.successful,
            "failed": stats.failed,
            "last_error": stats.last_error,
        }
        result["performance"] = {
            "avg_response_time_s": round(gateway.get_avg_response_time(), 3),
        }
    return result


@router.get("/connectivity")
async def connectivity_check():
    return get_connectivity()
