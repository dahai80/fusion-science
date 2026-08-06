from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[Any]] | None = None
    mcp_exposed: bool = True


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[..., Awaitable[Any]] | None = None,
        mcp_exposed: bool = True,
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            mcp_exposed=mcp_exposed,
        )
        logger.debug("Registered tool: %s", name)

    def unregister(self, name: str) -> None:
        if name in self._tools:
            del self._tools[name]
            logger.debug("Unregistered tool: %s", name)

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if not tool:
            logger.error("Tool not found: %s", name)
            return {"error": f"Tool '{name}' not found"}
        if not tool.handler:
            logger.error("Tool has no handler: %s", name)
            return {"error": f"Tool '{name}' has no handler registered"}
        try:
            result = await tool.handler(**arguments)
            logger.debug("Tool executed: %s", name)
            return result
        except Exception as e:
            logger.error("Tool execution failed: %s — %s", name, e)
            return {"error": str(e)}

    def get_openai_tools(self) -> list[dict]:
        result = []
        for tool in self._tools.values():
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return result

    def get_mcp_tools(self) -> list[dict]:
        result = []
        for tool in self._tools.values():
            if not tool.mcp_exposed:
                continue
            result.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters,
                }
            )
        return result

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools


def register_builtin_tools(registry: ToolRegistry, config: Any = None) -> None:
    registry.register(
        name="search_literature",
        description="Search academic databases for scientific literature (PubMed, arXiv)",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum results", "default": 20},
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sources to search (pubmed, arxiv)",
                    "default": ["pubmed", "arxiv"],
                },
            },
            "required": ["query"],
        },
        handler=_search_literature_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="search_database",
        description="Search scientific databases (UniProt, PDB, Ensembl, ChEMBL)",
        parameters={
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "description": "Database name (uniprot, pdb, ensembl, chembl)",
                },
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum results", "default": 20},
            },
            "required": ["database", "query"],
        },
        handler=_search_database_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="execute_python",
        description="Execute Python code for data analysis in a sandboxed environment",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "input_data": {
                    "type": "object",
                    "description": "Optional input data (passed as input_data variable)",
                },
            },
            "required": ["code"],
        },
        handler=_execute_python_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="generate_chart",
        description="Generate a publication-quality chart from data",
        parameters={
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "description": "Chart type (bar, line, scatter, box, violin, heatmap)",
                },
                "data_description": {"type": "string", "description": "Description of data to visualize"},
                "code": {"type": "string", "description": "Optional matplotlib/seaborn code"},
            },
            "required": ["chart_type", "data_description"],
        },
        handler=_generate_chart_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="fetch_paper",
        description="Fetch a paper by its identifier (PMID, DOI, or arXiv ID)",
        parameters={
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "Paper identifier (PMID, DOI, or arXiv ID)"},
                "id_type": {
                    "type": "string",
                    "description": "Type of identifier (pmid, doi, arxiv)",
                    "default": "auto",
                },
            },
            "required": ["identifier"],
        },
        handler=_fetch_paper_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="extract_findings",
        description="Extract structured findings (PICO, effect size, study type) from a paper's abstract or text",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Paper title"},
                "abstract": {"type": "string", "description": "Paper abstract or full text snippet"},
                "paper_id": {"type": "string", "description": "Paper identifier (PMID/DOI)", "default": ""},
            },
            "required": ["title", "abstract"],
        },
        handler=_extract_findings_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="analyze_consensus",
        description="Analyze consensus and contradictions across multiple study findings",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Research topic or question"},
                "findings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of finding statements or paper abstracts to synthesize",
                },
            },
            "required": ["topic", "findings"],
        },
        handler=_analyze_consensus_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="execute_r",
        description="Execute R code for statistical analysis (requires R+rpy2 installed)",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "R code to execute"},
                "capture_plots": {"type": "boolean", "description": "Whether to capture plots", "default": True},
            },
            "required": ["code"],
        },
        handler=_execute_r_handler,
        mcp_exposed=True,
    )

    logger.info("Registered %d builtin tools", len(registry.list_tools()))


async def _search_literature_handler(query: str, max_results: int = 20, sources: list[str] | None = None) -> dict:
    from ..literature.search import LiteratureSearch

    searcher = LiteratureSearch()
    try:
        result = await searcher.search(query, max_results=max_results, sources=sources)
        papers = []
        for p in result.papers:
            papers.append(
                {
                    "title": p.title,
                    "authors": p.authors,
                    "abstract": p.abstract[:500] if p.abstract else "",
                    "journal": p.journal,
                    "year": p.year,
                    "doi": p.doi,
                    "pmid": p.pmid,
                    "arxiv_id": p.arxiv_id,
                    "source": p.source,
                    "url": p.url,
                    "relevance_score": p.relevance_score,
                }
            )
        return {"papers": papers, "total_count": result.total_count, "sources_used": result.sources_used}
    except Exception as e:
        logger.error("search_literature failed: %s", e)
        return {"error": str(e), "papers": []}


async def _search_database_handler(database: str, query: str, max_results: int = 20) -> dict:
    connector_map = {
        "uniprot": ("fusion_science.database.uniprot", "UniProtConnector"),
        "pdb": ("fusion_science.database.pdb", "PDBConnector"),
        "ensembl": ("fusion_science.database.ensembl", "EnsemblConnector"),
        "chembl": ("fusion_science.database.chembl", "ChEMBLConnector"),
    }
    entry = connector_map.get(database.lower())
    if not entry:
        return {"error": f"Unknown database: {database}. Available: {list(connector_map.keys())}"}

    module_path, class_name = entry
    try:
        import importlib

        module = importlib.import_module(module_path)
        connector_cls = getattr(module, class_name)
        connector = connector_cls()
        try:
            result = await connector.search(query, max_results=max_results)
            return {
                "source": result.source,
                "items": result.items[:max_results],
                "total_count": result.total_count,
            }
        finally:
            await connector.close()
    except Exception as e:
        logger.error("search_database(%s) failed: %s", database, e)
        return {"error": str(e), "items": []}


async def _execute_python_handler(code: str, input_data: dict | None = None) -> dict:
    from ..compute.python_executor import PythonExecutor

    executor = PythonExecutor()
    try:
        result = await executor.execute(code, input_data=input_data)
        return {
            "success": result.success,
            "output": result.output[:5000] if result.output else "",
            "error": result.error[:2000] if result.error else "",
            "figures": result.figures,
            "execution_time": result.execution_time,
        }
    except Exception as e:
        logger.error("execute_python failed: %s", e)
        return {"success": False, "error": str(e)}


async def _generate_chart_handler(chart_type: str, data_description: str, code: str | None = None) -> dict:
    from ..compute.python_executor import PythonExecutor

    if not code:
        code = f"""
import matplotlib.pyplot as plt
import numpy as np

# Chart type: {chart_type}
# Data: {data_description}
# Replace the code below with your actual data and visualization

fig, ax = plt.subplots(figsize=(8, 6))
x = np.random.rand(20)
y = np.random.rand(20)

if "{chart_type}" == "bar":
    ax.bar(range(len(x)), x)
elif "{chart_type}" == "line":
    ax.plot(x)
elif "{chart_type}" == "scatter":
    ax.scatter(x, y)
elif "{chart_type}" == "box":
    ax.boxplot([x, y])
elif "{chart_type}" == "violin":
    ax.violinplot([x, y])
else:
    ax.scatter(x, y)

ax.set_title("{data_description}")
plt.tight_layout()
result = "Chart generated successfully"
"""
    executor = PythonExecutor()
    try:
        result = await executor.execute(code, capture_figures=True)
        return {
            "success": result.success,
            "output": result.output[:2000] if result.output else "",
            "error": result.error[:2000] if result.error else "",
            "figures": result.figures,
        }
    except Exception as e:
        logger.error("generate_chart failed: %s", e)
        return {"success": False, "error": str(e)}


async def _fetch_paper_handler(identifier: str, id_type: str = "auto") -> dict:
    if id_type == "auto":
        if identifier.isdigit():
            id_type = "pmid"
        elif identifier.startswith("10."):
            id_type = "doi"
        elif "arxiv" in identifier.lower() or identifier.count(".") >= 2:
            id_type = "arxiv"
        else:
            id_type = "pmid"

    if id_type == "pmid":
        from ..database.pubmed import PubMedConnector

        connector = PubMedConnector()
        try:
            result = await connector.fetch(identifier)
            return {"source": "pubmed", "items": result.items, "total_count": result.total_count}
        finally:
            await connector.close()
    elif id_type == "doi":
        from ..database.pubmed import PubMedConnector

        connector = PubMedConnector()
        try:
            result = await connector.search(identifier, max_results=1)
            if result.items:
                return {"source": "pubmed", "items": result.items[:1], "total_count": 1}
            return {"error": f"Paper not found for DOI: {identifier}"}
        finally:
            await connector.close()
    elif id_type == "arxiv":
        from ..literature.search import LiteratureSearch

        searcher = LiteratureSearch()
        try:
            result = await searcher._search_arxiv(identifier, max_results=1)
            if result.papers:
                p = result.papers[0]
                return {
                    "source": "arxiv",
                    "title": p.title,
                    "authors": p.authors,
                    "abstract": p.abstract,
                    "arxiv_id": p.arxiv_id,
                    "url": p.url,
                }
            return {"error": f"Paper not found for arXiv ID: {identifier}"}
        except Exception as e:
            return {"error": str(e)}
    else:
        return {"error": f"Unsupported id_type: {id_type}"}


async def _extract_findings_handler(title: str, abstract: str, paper_id: str = "") -> dict:
    from ..literature.extractor import LiteratureExtractor
    from ..literature.search import Paper

    extractor = LiteratureExtractor()
    paper = Paper(title=title, abstract=abstract, pmid=paper_id)
    try:
        extraction = await extractor.extract(paper, paper_id=paper_id)
        logger.info("extract_findings: type=%s for %s", extraction.study_type, paper_id or title[:40])
        return {
            "study_type": extraction.study_type,
            "pico": extraction.pico.__dict__,
            "sample_size": extraction.sample_size,
            "effect_size": extraction.effect_size,
            "p_value": extraction.p_value,
            "limitations": extraction.limitations,
            "funding_source": extraction.funding_source,
        }
    except Exception as e:
        logger.error("extract_findings failed: %s", e)
        return {"error": str(e)}


async def _analyze_consensus_handler(topic: str, findings: list[str]) -> dict:
    from ..literature.search import Paper
    from ..literature.synthesizer import LiteratureSynthesizer

    synthesizer = LiteratureSynthesizer()
    papers = [Paper(title=f"Finding {i + 1}", abstract=f) for i, f in enumerate(findings)]
    try:
        consensus = await synthesizer.synthesize(papers, topic=topic)
        logger.info(
            "analyze_consensus: score=%.2f, findings=%d", consensus.consensus_score, len(consensus.key_findings)
        )
        return {
            "topic": topic,
            "consensus_score": consensus.consensus_score,
            "key_findings": [f.statement for f in consensus.key_findings],
            "contradictions": [
                {"topic": c.topic, "position_a": c.position_a, "position_b": c.position_b}
                for c in consensus.contradictions
            ],
            "research_gaps": consensus.research_gaps,
        }
    except Exception as e:
        logger.error("analyze_consensus failed: %s", e)
        return {"error": str(e)}


async def _execute_r_handler(code: str, capture_plots: bool = True) -> dict:
    from ..compute.r_executor import RExecutor

    executor = RExecutor()
    if not executor.available:
        return {
            "success": False,
            "error": "R is not available. Install R and rpy2 (pip install fusion-science[r])",
        }
    try:
        result = await executor.execute(code, capture_plots=capture_plots)
        return {
            "success": result.success,
            "output": result.output[:5000] if result.output else "",
            "error": result.error[:2000] if result.error else "",
            "plots": result.plots,
            "execution_time": result.execution_time,
        }
    except Exception as e:
        logger.error("execute_r failed: %s", e)
        return {"success": False, "error": str(e)}
