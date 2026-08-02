# core/agents/data.py — DataAgent for data analysis tasks (F-22)
# Importers: core/agents/__init__.py, core/agents/router.py
# API: DataAgent extends ScienceAgent with data-specific tools + system prompt
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from ..agent import ScienceAgent
from ..engine import ScienceEngine
from ..tools import ToolRegistry

logger = logging.getLogger(__name__)

_DATA_SYSTEM_PROMPT = (
    "You are a data analysis specialist for scientific research. "
    "You write and execute code (Python/R) for statistical analysis, "
    "data transformation, and computational tasks. "
    "Always verify results, report confidence intervals when applicable, "
    "and flag potential data quality issues. "
    "Prefer established libraries (pandas, scipy, statsmodels, DESeq2)."
)


class DataAgent(ScienceAgent):
    def __init__(
        self,
        engine: ScienceEngine,
        tool_registry: ToolRegistry | None = None,
    ):
        tools = self._load_tools(tool_registry)
        super().__init__(
            name="data",
            engine=engine,
            system_prompt=_DATA_SYSTEM_PROMPT,
            tools=tools,
            tool_registry=tool_registry,
        )
        logger.info("DataAgent initialized with %d tools", len(tools))

    @staticmethod
    def _load_tools(tool_registry: ToolRegistry | None) -> list[dict]:
        if not tool_registry:
            return []
        tool_names = ["search_database", "execute_python", "execute_r"]
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
