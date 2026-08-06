# core/agents/literature.py — LiteratureAgent for literature search+extract+synthesize
# Instantiated by QueryRouterAgent._init_agents()
# Uses tools: search_literature, fetch_paper, extract_findings, analyze_consensus, search_database

from __future__ import annotations

import logging

from ..agent import ScienceAgent
from ..engine import ScienceEngine
from ..tools import ToolRegistry

logger = logging.getLogger(__name__)

_LITERATURE_SYSTEM_PROMPT = (
    "You are a literature research specialist. You search scientific databases, "
    "retrieve relevant papers, extract structured findings (PICO, effect sizes), "
    "analyze consensus and contradictions across studies, and generate literature reviews. "
    "Always cite your sources and provide evidence-based answers."
)

_LITERATURE_TOOLS = [
    "search_literature",
    "fetch_paper",
    "extract_findings",
    "analyze_consensus",
    "search_database",
]


class LiteratureAgent(ScienceAgent):
    def __init__(
        self,
        engine: ScienceEngine,
        tool_registry: ToolRegistry | None = None,
    ):
        tools = self._load_tools(tool_registry)
        super().__init__(
            name="literature",
            engine=engine,
            system_prompt=_LITERATURE_SYSTEM_PROMPT,
            tools=tools,
            tool_registry=tool_registry,
        )
        logger.debug("LiteratureAgent initialized with %d tools", len(tools))

    @staticmethod
    def _load_tools(tool_registry: ToolRegistry | None) -> list[dict]:
        if tool_registry:
            openai_tools = tool_registry.get_openai_tools()
            registry_map = {t["function"]["name"]: t for t in openai_tools}
            return [registry_map[n] for n in _LITERATURE_TOOLS if n in registry_map]
        return []
