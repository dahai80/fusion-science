from __future__ import annotations

import logging

from ..agent import AgentResult, ScienceAgent
from ..engine import ScienceEngine
from ..tools import ToolRegistry
from .data import DataAgent
from .error import ErrorAnalysisAgent
from .literature import LiteratureAgent
from .visualize import VizAgent
from .writer import WriterAgent

logger = logging.getLogger(__name__)

_ROUTE_KEYWORDS: dict[str, list[str]] = {
    "literature": [
        "search",
        "find",
        "retrieve",
        "paper",
        "literature",
        "pubmed",
        "review",
        "citation",
        "pico",
        "consensus",
        "synthesi",
    ],
    "data": [
        "analyze",
        "analysis",
        "compute",
        "calculate",
        "statistics",
        "python",
        "code",
        "execute",
        "run",
        "deseq",
        "correlation",
    ],
    "visualize": [
        "chart",
        "plot",
        "figure",
        "visual",
        "graph",
        "heatmap",
        "volcano",
        "molecule",
        "protein",
        "3d",
    ],
    "writer": [
        "write",
        "draft",
        "compose",
        "section",
        "introduction",
        "methods",
        "discussion",
        "conclusion",
        "cite",
    ],
}


class QueryRouterAgent:
    # Called by api/routes/search.py, analysis.py, visualize.py, review.py
    # Instantiates all 5 agents, routes by keyword matching
    def __init__(
        self,
        engine: ScienceEngine,
        tool_registry: ToolRegistry | None = None,
    ):
        self.engine = engine
        self.tool_registry = tool_registry
        self._agents: dict[str, ScienceAgent] = {}
        self._init_agents()

    def _init_agents(self) -> None:
        self._agents["literature"] = LiteratureAgent(
            engine=self.engine,
            tool_registry=self.tool_registry,
        )
        self._agents["data"] = DataAgent(
            engine=self.engine,
            tool_registry=self.tool_registry,
        )
        self._agents["visualize"] = VizAgent(
            engine=self.engine,
            tool_registry=self.tool_registry,
        )
        self._agents["writer"] = WriterAgent(
            engine=self.engine,
            tool_registry=self.tool_registry,
        )
        self._agents["error"] = ErrorAnalysisAgent(
            engine=self.engine,
            tool_registry=self.tool_registry,
        )
        logger.info("QueryRouterAgent initialized with %d agents", len(self._agents))

    def route(self, query: str) -> str:
        query_lower = query.lower()
        scores: dict[str, int] = {}
        for agent_name, keywords in _ROUTE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in query_lower)
            scores[agent_name] = score

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        if scores[best] == 0:
            best = "literature"

        logger.info("Routed query to '%s' (scores: %s)", best, scores)
        return best

    async def dispatch(self, query: str, max_iterations: int = 10) -> AgentResult:
        agent_name = self.route(query)
        agent = self._agents.get(agent_name)
        if not agent:
            logger.error("Agent '%s' not found", agent_name)
            return AgentResult(
                agent_name=agent_name,
                output="",
                error=f"Agent '{agent_name}' not available",
            )
        logger.info("Dispatching to %s: %s", agent_name, query[:100])
        try:
            result = await agent.run(query, max_iterations=max_iterations)
            return result
        except Exception as e:
            logger.exception("Agent %s failed", agent_name)
            error_agent = self._agents.get("error")
            if error_agent and agent_name != "error":
                logger.info("Escalating to ErrorAnalysisAgent")
                try:
                    return await error_agent.run(
                        f"Agent '{agent_name}' failed on query: {query}\nError: {e}",
                    )
                except Exception:
                    pass
            return AgentResult(
                agent_name=agent_name,
                output="",
                error=str(e),
            )

    def get_agent(self, name: str) -> ScienceAgent | None:
        return self._agents.get(name)

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())
