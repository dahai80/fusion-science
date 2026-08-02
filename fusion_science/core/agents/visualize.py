# core/agents/visualize.py — VizAgent for visualization tasks (F-22)
# Importers: core/agents/__init__.py, core/agents/router.py
# API: VizAgent extends ScienceAgent with visualization tools + system prompt
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from ..agent import ScienceAgent
from ..engine import ScienceEngine
from ..tools import ToolRegistry

logger = logging.getLogger(__name__)

_VIZ_SYSTEM_PROMPT = (
    "You are a scientific visualization specialist. "
    "You create publication-quality charts, molecular structure visualizations, "
    "and protein structure renderings. "
    "Follow best practices: proper axis labels, legends, color-blind-friendly palettes. "
    "For molecules, choose appropriate styles (stick, sphere, cartoon). "
    "For proteins, select color schemes that highlight structural features."
)


class VizAgent(ScienceAgent):
    def __init__(
        self,
        engine: ScienceEngine,
        tool_registry: ToolRegistry | None = None,
    ):
        tools = self._load_tools(tool_registry)
        super().__init__(
            name="visualize",
            engine=engine,
            system_prompt=_VIZ_SYSTEM_PROMPT,
            tools=tools,
            tool_registry=tool_registry,
        )
        logger.info("VizAgent initialized with %d tools", len(tools))

    @staticmethod
    def _load_tools(tool_registry: ToolRegistry | None) -> list[dict]:
        if not tool_registry:
            return []
        tool_names = ["generate_chart", "visualize_molecule", "visualize_protein"]
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
