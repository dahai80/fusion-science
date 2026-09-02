from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Cap on a tool result before it is fed back into the agent prompt context.
_MAX_RESULT_CHARS = 8000

# I-9: shared LLMGateway bound to the literature tool handlers at registration
# time. Without this the extract_findings / analyze_consensus tools construct
# their own gateway-less instances and silently degrade to rule-based logic.
_LITERATURE_GATEWAY: Any = None


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
        # R-6: validate arguments against the tool's declared schema before
        # dispatching. A malformed LLM tool_call (missing required arg, wrong
        # type) otherwise reaches the handler as a confusing TypeError that
        # looks like an internal bug. Reject early with a clear contract error.
        if not isinstance(arguments, dict):
            return {"error": f"Tool '{name}' expects an object, got {type(arguments).__name__}"}
        missing = self._missing_required(tool, arguments)
        if missing:
            logger.warning("Tool %s rejected: missing required args %s", name, missing)
            return {"error": f"Tool '{name}' missing required arguments: {missing}"}
        type_error = self._check_types(tool, arguments)
        if type_error:
            logger.warning("Tool %s rejected: %s", name, type_error)
            return {"error": f"Tool '{name}' argument type error: {type_error}"}
        try:
            result = await tool.handler(**arguments)
            logger.debug("Tool executed: %s", name)
            return self._cap_result(name, result)
        except Exception as e:
            logger.error("Tool execution failed: %s — %s", name, e)
            return {"error": str(e)}

    @staticmethod
    def _missing_required(tool: ToolDefinition, arguments: dict[str, Any]) -> list[str]:
        required = tool.parameters.get("required") if tool.parameters else None
        if not required:
            return []
        return [r for r in required if r not in arguments or arguments[r] is None]

    @staticmethod
    def _check_types(tool: ToolDefinition, arguments: dict[str, Any]) -> str:
        props = tool.parameters.get("properties") if tool.parameters else None
        if not props:
            return ""
        _PY = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
        for key, value in arguments.items():
            schema = props.get(key)
            if not schema or not isinstance(schema, dict):
                continue
            expected = schema.get("type")
            if not expected:
                continue
            allowed = _PY.get(expected)
            if allowed is None:
                continue
            # bool is a subclass of int in Python; only accept bool for boolean,
            # not for integer/number, so "true" doesn't satisfy an int param.
            if expected in ("integer", "number") and isinstance(value, bool):
                return f"'{key}' expected {expected}, got boolean"
            if not isinstance(value, allowed):
                return f"'{key}' expected {expected}, got {type(value).__name__}"
        return ""

    @staticmethod
    def _cap_result(name: str, result: Any) -> Any:
        # Bound the size of a tool result fed back into the agent context so a
        # single huge DB dump cannot blow the prompt budget. Agent.run also
        # truncates, but capping here avoids serializing megabytes first.
        try:
            import json

            encoded = json.dumps(result, default=str, ensure_ascii=False)
            if len(encoded) > _MAX_RESULT_CHARS:
                logger.warning("Tool %s result capped: %d -> %d chars", name, len(encoded), _MAX_RESULT_CHARS)
                return {"_truncated": True, "preview": encoded[:_MAX_RESULT_CHARS] + "...[truncated]"}
        except (TypeError, ValueError):
            pass
        return result

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


def register_builtin_tools(registry: ToolRegistry, config: Any = None, gateway: Any = None) -> None:
    global _LITERATURE_GATEWAY
    _LITERATURE_GATEWAY = gateway
    if gateway is None:
        logger.warning(
            "register_builtin_tools: no LLMGateway bound — extract_findings and "
            "analyze_consensus will run in rule-based/offline mode, not LLM mode."
        )
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
        # R-6: arbitrary code execution must NOT be exposed over MCP — an MCP
        # caller could run unbounded code on the host. Internal agent loop only.
        mcp_exposed=False,
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
        # R-6: arbitrary R code execution — not exposed over MCP (same RCE risk
        # as execute_python). Internal agent loop only.
        mcp_exposed=False,
    )

    registry.register(
        name="visualize_molecule",
        description="Render a 2D/3D molecular structure from a SMILES string (RDKit/py3Dmol, 2D fallback)",
        parameters={
            "type": "object",
            "properties": {
                "smiles": {"type": "string", "description": "SMILES notation of the molecule"},
                "name": {"type": "string", "description": "Display name for the molecule", "default": "molecule"},
            },
            "required": ["smiles"],
        },
        handler=_visualize_molecule_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="visualize_protein",
        description="Render a 3D protein structure from a PDB ID or PDB content (py3Dmol, cartoon/surface/ribbon)",
        parameters={
            "type": "object",
            "properties": {
                "pdb_id": {"type": "string", "description": "PDB ID (e.g., 6M0J)"},
                "style": {
                    "type": "string",
                    "description": "Visualization style (cartoon, surface, ribbon)",
                    "default": "cartoon",
                },
                "show_ligands": {"type": "boolean", "description": "Show bound ligands", "default": True},
            },
            "required": ["pdb_id"],
        },
        handler=_visualize_protein_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="explain_math",
        description="Explain a mathematical/statistical formula: identify type, render LaTeX, describe variables",
        parameters={
            "type": "object",
            "properties": {
                "formula": {"type": "string", "description": "Formula or statistical expression to explain"},
            },
            "required": ["formula"],
        },
        handler=_explain_math_handler,
        mcp_exposed=True,
    )

    registry.register(
        name="generate_citation",
        description="Format a citation for a paper in APA, Vancouver, or BibTeX style",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Paper title"},
                "authors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Author names",
                    "default": [],
                },
                "year": {"type": "string", "description": "Publication year", "default": ""},
                "journal": {"type": "string", "description": "Journal name", "default": ""},
                "doi": {"type": "string", "description": "Digital Object Identifier", "default": ""},
                "style": {
                    "type": "string",
                    "description": "Citation style (apa, vancouver, bibtex)",
                    "default": "apa",
                },
            },
            "required": ["title"],
        },
        handler=_generate_citation_handler,
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


_CHART_TYPES = {"bar", "line", "scatter", "box", "violin", "heatmap"}


async def _generate_chart_handler(chart_type: str, data_description: str, code: str | None = None) -> dict:
    from ..compute.python_executor import PythonExecutor

    if chart_type not in _CHART_TYPES:
        logger.warning("Rejected unknown chart_type: %r", chart_type)
        return {"success": False, "error": f"Unsupported chart_type: {chart_type}"}

    if not code:
        # data_description passed as input_data (never inlined into source)
        code = """
import matplotlib.pyplot as plt
import numpy as np

chart_type = input_data.get('chart_type', 'scatter')
title = input_data.get('title', '')

fig, ax = plt.subplots(figsize=(8, 6))
x = np.random.rand(20)
y = np.random.rand(20)

if chart_type == "bar":
    ax.bar(range(len(x)), x)
elif chart_type == "line":
    ax.plot(x)
elif chart_type == "scatter":
    ax.scatter(x, y)
elif chart_type == "box":
    ax.boxplot([x, y])
elif chart_type == "violin":
    ax.violinplot([x, y])
else:
    ax.scatter(x, y)

ax.set_title(title)
plt.tight_layout()
result = "Chart generated successfully"
"""
    executor = PythonExecutor()
    try:
        result = await executor.execute(
            code,
            input_data={"chart_type": chart_type, "title": data_description[:200]},
            capture_figures=True,
        )
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

    # I-9: bind the shared LLMGateway captured at register time. Previously
    # this handler instantiated LiteratureExtractor() with no gateway, so the
    # extract_findings tool ALWAYS fell back to rule-based extraction and never
    # invoked the LLM — the tool silently lied about using the model.
    extractor = LiteratureExtractor(gateway=_LITERATURE_GATEWAY)
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

    # I-9: bind the shared LLMGateway (see _extract_findings_handler note).
    synthesizer = LiteratureSynthesizer(gateway=_LITERATURE_GATEWAY)
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


async def _visualize_molecule_handler(smiles: str, name: str = "molecule") -> dict:
    from ..visualization.molecule import MoleculeVisualizer

    visualizer = MoleculeVisualizer()
    try:
        result = await visualizer.from_smiles(smiles, name=name)
        logger.info("visualize_molecule: smiles=%s, error=%s", smiles[:40], bool(result.error))
        return {
            "success": result.success,
            "name": name,
            "smiles": result.smiles,
            "html_path": result.html_path,
            "image_path": result.image_path,
            "pdb_path": result.pdb_path,
            "error": result.error,
        }
    except Exception as e:
        logger.error("visualize_molecule failed: %s", e)
        return {"error": str(e)}


async def _visualize_protein_handler(pdb_id: str, style: str = "cartoon", show_ligands: bool = True) -> dict:
    from ..visualization.protein import ProteinVisualizer

    visualizer = ProteinVisualizer()
    try:
        result = await visualizer.visualize(pdb_id=pdb_id, style=style, show_ligands=show_ligands)
        logger.info("visualize_protein: pdb_id=%s, style=%s, error=%s", pdb_id, style, bool(result.error))
        return {
            "pdb_id": result.pdb_id,
            "html_path": getattr(result, "html_path", ""),
            "error": getattr(result, "error", ""),
        }
    except Exception as e:
        logger.error("visualize_protein failed: %s", e)
        return {"error": str(e)}


async def _explain_math_handler(formula: str) -> dict:
    from ..literature.math_explainer import MathExplainer

    explainer = MathExplainer()
    try:
        explanation = explainer.explain(formula)
        logger.info("explain_math: formula=%s, name=%s", formula[:40], explanation.name)
        return explanation.to_dict()
    except Exception as e:
        logger.error("explain_math failed: %s", e)
        return {"error": str(e)}


async def _generate_citation_handler(
    title: str,
    authors: list[str] | None = None,
    year: str = "",
    journal: str = "",
    doi: str = "",
    style: str = "apa",
) -> dict:
    from ..literature.citation import CitationManager
    from ..literature.search import Paper

    manager = CitationManager()
    paper = Paper(
        title=title,
        authors=authors or [],
        year=year,
        journal=journal,
        doi=doi,
    )
    manager.add_paper(paper)
    try:
        style_lower = style.lower()
        if style_lower == "vancouver":
            citation = manager.format_vancouver(paper)
        elif style_lower == "bibtex":
            citation = manager.format_bibtex(paper)
        else:
            citation = manager.format_apa(paper)
        logger.info("generate_citation: style=%s, title=%s", style_lower, title[:40])
        return {"style": style_lower, "citation": citation}
    except Exception as e:
        logger.error("generate_citation failed: %s", e)
        return {"error": str(e)}
