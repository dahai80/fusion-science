# api/routes/search.py — POST /api/v1/search
# Importers: api/app.py includes router
# API: SearchRequest(query, max_results, sources) → search results
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...core.tools import ToolRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    max_results: int = Field(default=20, ge=1, le=100)
    sources: list[str] | None = None


@router.post("")
async def search(req: SearchRequest, request: Request):
    tool_registry: ToolRegistry | None = getattr(request.app.state, "tool_registry", None)
    if not tool_registry or not tool_registry.has_tool("search_literature"):
        return {"error": "search_literature tool not available"}
    args = {"query": req.query, "max_results": req.max_results}
    if req.sources:
        args["sources"] = req.sources
    result = await tool_registry.execute("search_literature", args)
    return result
