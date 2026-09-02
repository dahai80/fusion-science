from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .._owner import check_owner

logger = logging.getLogger(__name__)
router = APIRouter()


class VisualizeRequest(BaseModel):
    query: str
    chart_type: str = Field(default="scatter")
    data_description: str = Field(default="")
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("/visualize")
async def visualize(session_id: str, request: Request, req: VisualizeRequest):
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied

    router_agent = getattr(request.app.state, "router_agent", None)
    if not router_agent:
        return {"error": "router_agent not available"}
    viz_agent = router_agent.get_agent("visualize")
    if not viz_agent:
        return {"error": "visualize agent not available"}
    result = await viz_agent.run(req.query, max_iterations=req.max_iterations)
    return {
        "agent": result.agent_name,
        "output": result.output,
        "error": result.error,
        "duration": result.duration,
    }
