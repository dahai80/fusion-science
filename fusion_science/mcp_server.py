# mcp_server.py — MCP JSON-RPC 2.0 endpoint (F-21)
# Importers: api/app.py mounts router at /mcp
# API: POST / with JSON-RPC methods: initialize, tools/list, tools/call
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()

_PROTOCOL_VERSION = "2024-11-05"


@router.post("/")
async def handle_mcp(request: Request):
    body = await request.body()
    try:
        msg = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response(None, -32700, "Parse error")

    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    if not method:
        return _error_response(req_id, -32600, "Invalid Request: missing method")

    logger.info("MCP request: method=%s id=%s", method, req_id)

    if method == "initialize":
        return _success_response(req_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fusion-science-mcp", "version": "0.4.0"},
        })

    if method == "tools/list":
        tool_registry = getattr(request.app.state, "tool_registry", None)
        if not tool_registry:
            return _success_response(req_id, {"tools": []})
        return _success_response(req_id, {"tools": tool_registry.get_mcp_tools()})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not tool_name:
            return _error_response(req_id, -32602, "Invalid params: missing tool name")
        tool_registry = getattr(request.app.state, "tool_registry", None)
        if not tool_registry:
            return _error_response(req_id, -32603, "Internal error: tool registry not available")
        try:
            result = await tool_registry.execute(tool_name, arguments)
            return _success_response(req_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]})
        except Exception as e:
            logger.error("MCP tools/call failed: %s", e)
            return _error_response(req_id, -32603, f"Internal error: {e}")

    return _error_response(req_id, -32601, f"Method not found: {method}")


def _success_response(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error_response(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
