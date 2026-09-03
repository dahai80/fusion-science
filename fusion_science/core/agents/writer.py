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
        # F-C2: write_section / manage_citations are not registered in the
        # ToolRegistry (the 12 built-in tools cover search/compute/viz/citation).
        # Rather than declare tools the agent cannot actually call, WriterAgent
        # is a prompt-only writing agent. It relies on generate_citation (which
        # IS registered) when the model needs citation formatting. Log the gap
        # so it is not silent.
        if not tool_registry:
            logger.info("WriterAgent: no tool_registry, running prompt-only")
            return []
        wanted = ["write_section", "manage_citations", "generate_citation"]
        result = []
        for name in wanted:
            td = tool_registry.get_tool(name)
            if td:
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": td.name,
                            "description": td.description,
                            "parameters": td.parameters,
                        },
                    }
                )
            else:
                logger.info("WriterAgent: tool '%s' not registered, running without it", name)
        return result
