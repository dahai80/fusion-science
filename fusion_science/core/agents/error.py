# core/agents/error.py — ErrorAnalysisAgent for failure diagnosis (F-22)
# Importers: core/agents/__init__.py, core/agents/router.py
# API: ErrorAnalysisAgent extends ScienceAgent with error diagnosis tools
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from ..agent import ScienceAgent
from ..engine import ScienceEngine
from ..tools import ToolRegistry

logger = logging.getLogger(__name__)

_ERROR_SYSTEM_PROMPT = (
    "You are an error analysis specialist for scientific computing. "
    "When other agents encounter failures, you diagnose the root cause, "
    "suggest fixes, and verify corrections. "
    "Analyze error messages, stack traces, and context to provide "
    "actionable debugging guidance. "
    "If possible, provide corrected code that resolves the issue."
)


class ErrorAnalysisAgent(ScienceAgent):
    def __init__(
        self,
        engine: ScienceEngine,
        tool_registry: ToolRegistry | None = None,
    ):
        tools = self._load_tools(tool_registry)
        super().__init__(
            name="error",
            engine=engine,
            system_prompt=_ERROR_SYSTEM_PROMPT,
            tools=tools,
            tool_registry=tool_registry,
        )
        logger.info("ErrorAnalysisAgent initialized with %d tools", len(tools))

    @staticmethod
    def _load_tools(tool_registry: ToolRegistry | None) -> list[dict]:
        if not tool_registry:
            return []
        tool_names = ["execute_python"]
        result = []
        for name in tool_names:
            td = tool_registry.get_tool(name)
            if td:
                result.append({
                    "type": "function",
                    "function": {
                        "name": td.name,
                        "description": td.description,
                        "parameters": td.parameters,
                    },
                })
        return result
