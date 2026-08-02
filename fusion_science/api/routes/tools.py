from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class RegisterToolRequest(BaseModel):
    name: str
    description: str
    parameters: dict
    mcp_exposed: bool = True


@router.get("")
async def list_tools(request: Request):
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is None:
        return {"tools": [], "total": 0}
    names = tool_registry.list_tools()
    tools = []
    for name in names:
        td = tool_registry.get_tool(name)
        if td:
            tools.append({
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
                "mcp_exposed": td.mcp_exposed,
                "has_handler": td.handler is not None,
            })
    return {"tools": tools, "total": len(tools)}


@router.post("")
async def register_tool(body: RegisterToolRequest, request: Request):
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is None:
        from ...core.tools import ToolRegistry
        tool_registry = ToolRegistry()
        request.app.state.tool_registry = tool_registry
    if tool_registry.has_tool(body.name):
        return {"error": f"Tool '{body.name}' already registered"}
    tool_registry.register(
        name=body.name,
        description=body.description,
        parameters=body.parameters,
        handler=None,
        mcp_exposed=body.mcp_exposed,
    )
    logger.info("Custom tool registered via API: %s", body.name)
    return {"registered": body.name}


@router.delete("/{tool_name}")
async def unregister_tool(tool_name: str, request: Request):
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is None or not tool_registry.has_tool(tool_name):
        return {"error": f"Tool '{tool_name}' not found"}
    tool_registry.unregister(tool_name)
    logger.info("Tool unregistered via API: %s", tool_name)
    return {"unregistered": tool_name}


@router.get("/{tool_name}")
async def get_tool(tool_name: str, request: Request):
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is None or not tool_registry.has_tool(tool_name):
        return {"error": f"Tool '{tool_name}' not found"}
    td = tool_registry.get_tool(tool_name)
    return {
        "name": td.name,
        "description": td.description,
        "parameters": td.parameters,
        "mcp_exposed": td.mcp_exposed,
        "has_handler": td.handler is not None,
    }
