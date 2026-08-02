# core/agents/writer.py — WriterAgent for paper writing tasks (F-22)
# Importers: core/agents/__init__.py, core/agents/router.py
# API: WriterAgent extends ScienceAgent with writing tools + system prompt
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from ..agent import ScienceAgent
from ..engine import ScienceEngine
from ..tools import ToolRegistry

logger = logging.getLogger(__name__)

_WRITER_SYSTEM_PROMPT = (
    "You are a scientific writing specialist. "
    "You draft and refine sections of research papers, manage citations, "
    "and ensure academic writing standards. "
    "Follow IMRaD structure conventions. "
    "Use precise scientific language, avoid unsupported claims, "
    "and properly attribute all findings to their sources."
)


class WriterAgent(ScienceAgent):
    def __init__(
        self,
        engine: ScienceEngine,
        tool_registry: ToolRegistry | None = None,
    ):
        tools = self._load_tools(tool_registry)
        super().__init__(
            name="writer",
            engine=engine,
            system_prompt=_WRITER_SYSTEM_PROMPT,
            tools=tools,
            tool_registry=tool_registry,
        )
        logger.info("WriterAgent initialized with %d tools", len(tools))

    @staticmethod
    def _load_tools(tool_registry: ToolRegistry | None) -> list[dict]:
        if not tool_registry:
            return []
        tool_names = ["write_section", "manage_citations"]
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
