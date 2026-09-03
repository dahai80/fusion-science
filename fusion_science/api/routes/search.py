# api/routes/search.py — POST /api/v1/sessions/{id}/search
# Importers: api/app.py includes router; consumed by fusion-studio ScienceBridge
# API: SearchRequest(query, max_results, sources) -> literature search result
# Data: stores result into session.context.papers + Artifact(type="search_result")
# Issue #7: search 产出注入 session 上下文，供后续 analyze/review 引用

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...core.tools import ToolRegistry
from ...session.models import Artifact
from .._owner import check_owner

logger = logging.getLogger(__name__)
router = APIRouter()


class SearchRequest(BaseModel):
    # R-7: cap query length; bound sources list size.
    query: str = Field(..., max_length=8000)
    max_results: int = Field(default=20, ge=1, le=100)
    sources: list[str] | None = Field(default=None, max_length=20)


@router.post("/search")
async def search(session_id: str, request: Request, req: SearchRequest):
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied

    tool_registry: ToolRegistry | None = getattr(request.app.state, "tool_registry", None)
    if not tool_registry or not tool_registry.has_tool("search_literature"):
        # R-8: tool absent is a server misconfiguration, not a 200 error body.
        raise HTTPException(status_code=503, detail="search_literature tool not available")
    args = {"query": req.query, "max_results": req.max_results}
    if req.sources:
        args["sources"] = req.sources
    result = await tool_registry.execute("search_literature", args)

    papers = result.get("papers", []) if isinstance(result, dict) else []
    if papers:
        try:
            session.context.papers = papers
            session.updated_at = time.time()
            await mgr.save(session)
            artifact = Artifact(
                id=f"search_{int(session.updated_at)}",
                type="search_result",
                name=req.query[:80],
                content=json.dumps(
                    {"query": req.query, "total_count": result.get("total_count", len(papers))},
                    ensure_ascii=False,
                ),
                metadata={"paper_count": len(papers), "query": req.query},
            )
            await mgr.add_artifact(session_id, artifact)
            logger.info("search: %d papers stored into session %s context", len(papers), session_id)
        except Exception as e:
            logger.warning("search: failed to persist context into session %s: %s", session_id, e)

    result["session_id"] = session_id
    result["context_papers"] = len(papers)
    return result
