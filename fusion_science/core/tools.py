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
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return result

    def get_mcp_tools(self) -> list[dict]:
        result = []
        for tool in self._tools.values():
            if not tool.mcp_exposed:
                continue
            result.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
            })
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

    # F-22: extract_findings — called by LiteratureAgent, DataAgent
    registry.register(
        name="extract_findings",
        description="Extract structured findings (PICO, effect size, methodology) from papers",
        parameters={
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of paper objects with title, abstract, etc.",
                },
                "extraction_type": {
                    "type": "string",
                    "description": "Type of extraction (pico, structured, all)",
                    "default": "all",
                },
            },
            "required": ["papers"],
        },
        handler=_extract_findings_handler,
        mcp_exposed=True,
    )

    # F-22: analyze_consensus — called by LiteratureAgent
    registry.register(
        name="analyze_consensus",
        description="Analyze consensus, contradictions, and research gaps across multiple papers",
        parameters={
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of paper objects to analyze for consensus",
                },
                "topic": {
                    "type": "string",
                    "description": "Research topic or question for consensus analysis",
                },
            },
            "required": ["papers", "topic"],
        },
        handler=_analyze_consensus_handler,
        mcp_exposed=True,
    )

    # F-22: execute_r — called by DataAgent
    registry.register(
        name="execute_r",
        description="Execute R code for statistical analysis via rpy2",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "R code to execute"},
                "input_data": {
                    "type": "object",
                    "description": "Optional input data passed as R variables",
                },
            },
            "required": ["code"],
        },
        handler=_execute_r_handler,
        mcp_exposed=True,
    )

    # F-22: visualize_molecule — called by VizAgent
    registry.register(
        name="visualize_molecule",
        description="Generate 3D molecular structure visualization from SMILES or molecular data",
        parameters={
            "type": "object",
            "properties": {
                "smiles": {
                    "type": "string",
                    "description": "SMILES string representing the molecule",
                },
                "molecule_name": {
                    "type": "string",
                    "description": "Name of the molecule for display",
                    "default": "",
                },
                "style": {
                    "type": "string",
                    "description": "Visualization style (stick, sphere, cartoon)",
                    "default": "stick",
                },
            },
            "required": ["smiles"],
        },
        handler=_visualize_molecule_handler,
        mcp_exposed=True,
    )

    # F-22: visualize_protein — called by VizAgent
    registry.register(
        name="visualize_protein",
        description="Generate 3D protein structure visualization from PDB data",
        parameters={
            "type": "object",
            "properties": {
                "pdb_id": {
                    "type": "string",
                    "description": "PDB identifier for the protein structure",
                },
                "style": {
                    "type": "string",
                    "description": "Visualization style (cartoon, stick, sphere)",
                    "default": "cartoon",
                },
                "color_scheme": {
                    "type": "string",
                    "description": "Color scheme (chain, ss, residue)",
                    "default": "chain",
                },
            },
            "required": ["pdb_id"],
        },
        handler=_visualize_protein_handler,
        mcp_exposed=True,
    )

    # F-22: write_section — internal, called by WriterAgent
    registry.register(
        name="write_section",
        description="Write a section of a scientific paper (Introduction, Methods, Results, Discussion)",
        parameters={
            "type": "object",
            "properties": {
                "section_type": {
                    "type": "string",
                    "description": "Section type (introduction, methods, results, discussion, conclusion)",
                },
                "content_brief": {
                    "type": "string",
                    "description": "Brief description of what the section should cover",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context from previous sections or findings",
                    "default": "",
                },
            },
            "required": ["section_type", "content_brief"],
        },
        handler=_write_section_handler,
        mcp_exposed=False,
    )

    # F-22: manage_citations — internal, called by WriterAgent
    registry.register(
        name="manage_citations",
        description="Format and manage citations in various styles (APA, Vancouver, BibTeX)",
        parameters={
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of paper objects with title, authors, year, journal, doi",
                },
                "style": {
                    "type": "string",
                    "description": "Citation style (apa, vancouver, bibtex)",
                    "default": "apa",
                },
                "action": {
                    "type": "string",
                    "description": "Action (format, deduplicate, graph)",
                    "default": "format",
                },
            },
            "required": ["papers"],
        },
        handler=_manage_citations_handler,
        mcp_exposed=False,
    )

    logger.info("Registered %d builtin tools", len(registry.list_tools()))


async def _search_literature_handler(query: str, max_results: int = 20, sources: list[str] | None = None) -> dict:
    from ..literature.search import LiteratureSearch
    searcher = LiteratureSearch()
    try:
        result = await searcher.search(query, max_results=max_results, sources=sources)
        papers = []
        for p in result.papers:
            papers.append({
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
            })
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


async def _extract_findings_handler(
    papers: list[dict],
    extraction_type: str = "all",
) -> dict:
    from ..literature.extractor import LiteratureExtractor
    extractor = LiteratureExtractor()
    try:
        results = []
        for paper in papers[:30]:
            extraction = await extractor.extract(
                title=paper.get("title", ""),
                abstract=paper.get("abstract", ""),
                extraction_type=extraction_type,
            )
            results.append({
                "title": paper.get("title", ""),
                "extraction": extraction.to_dict() if hasattr(extraction, "to_dict") else extraction,
            })
        return {"extractions": results, "count": len(results)}
    except Exception as e:
        logger.error("extract_findings failed: %s", e)
        return {"error": str(e), "extractions": []}


async def _analyze_consensus_handler(
    papers: list[dict],
    topic: str,
) -> dict:
    from ..literature.synthesizer import LiteratureSynthesizer
    synthesizer = LiteratureSynthesizer()
    try:
        analysis = await synthesizer.synthesize(papers=papers, topic=topic)
        if hasattr(analysis, "to_dict"):
            return {"consensus": analysis.to_dict(), "topic": topic}
        return {"consensus": analysis, "topic": topic}
    except Exception as e:
        logger.error("analyze_consensus failed: %s", e)
        return {"error": str(e)}


async def _execute_r_handler(code: str, input_data: dict | None = None) -> dict:
    from ..compute.r_executor import RExecutor
    executor = RExecutor()
    try:
        result = await executor.execute(code, input_data=input_data)
        return {
            "success": result.success,
            "output": result.output[:5000] if result.output else "",
            "error": result.error[:2000] if result.error else "",
            "figures": result.figures if hasattr(result, "figures") else [],
            "execution_time": result.execution_time if hasattr(result, "execution_time") else 0.0,
        }
    except Exception as e:
        logger.error("execute_r failed: %s", e)
        return {"success": False, "error": str(e)}


async def _visualize_molecule_handler(
    smiles: str,
    molecule_name: str = "",
    style: str = "stick",
) -> dict:
    from ..visualization.molecule import MoleculeVisualizer
    visualizer = MoleculeVisualizer()
    try:
        result = await visualizer.visualize(
            smiles=smiles,
            name=molecule_name or smiles,
            style=style,
        )
        if hasattr(result, "to_dict"):
            return {"molecule": result.to_dict(), "smiles": smiles}
        return {"molecule": result, "smiles": smiles}
    except Exception as e:
        logger.error("visualize_molecule failed: %s", e)
        return {"error": str(e), "smiles": smiles}


async def _visualize_protein_handler(
    pdb_id: str,
    style: str = "cartoon",
    color_scheme: str = "chain",
) -> dict:
    from ..visualization.protein import ProteinVisualizer
    visualizer = ProteinVisualizer()
    try:
        result = await visualizer.visualize(
            pdb_id=pdb_id,
            style=style,
            color_scheme=color_scheme,
        )
        if hasattr(result, "to_dict"):
            return {"protein": result.to_dict(), "pdb_id": pdb_id}
        return {"protein": result, "pdb_id": pdb_id}
    except Exception as e:
        logger.error("visualize_protein failed: %s", e)
        return {"error": str(e), "pdb_id": pdb_id}


async def _write_section_handler(
    section_type: str,
    content_brief: str,
    context: str = "",
) -> dict:
    from ..config import ScienceConfig
    from ..core.gateway import LLMGateway
    config = ScienceConfig.from_env()
    gateway = LLMGateway(config)
    try:
        section_prompts = {
            "introduction": "Write a scientific paper Introduction section",
            "methods": "Write a scientific paper Methods section with precise methodology details",
            "results": "Write a scientific paper Results section reporting findings objectively",
            "discussion": "Write a scientific paper Discussion section interpreting findings and implications",
            "conclusion": "Write a scientific paper Conclusion section summarizing key takeaways",
        }
        prompt = section_prompts.get(section_type, f"Write a scientific paper {section_type} section")
        if context:
            prompt += f"\n\nContext from previous sections:\n{context}"
        prompt += f"\n\nContent to cover: {content_brief}"
        messages = gateway.build_science_prompt(prompt)
        resp = await gateway.chat(messages, temperature=0.4)
        return {"section_type": section_type, "content": resp.content, "model": resp.model}
    except Exception as e:
        logger.error("write_section failed: %s", e)
        return {"error": str(e), "section_type": section_type}
    finally:
        await gateway.close()


async def _manage_citations_handler(
    papers: list[dict],
    style: str = "apa",
    action: str = "format",
) -> dict:
    from ..literature.citation import CitationManager
    manager = CitationManager()
    try:
        if action == "format":
            formatted = []
            for paper in papers:
                citation = manager.format_citation(paper, style=style)
                formatted.append(citation)
            return {"citations": formatted, "style": style, "count": len(formatted)}
        elif action == "deduplicate":
            deduped = manager.deduplicate(papers)
            return {"papers": deduped, "original_count": len(papers), "deduped_count": len(deduped)}
        elif action == "graph":
            graph = manager.build_citation_graph(papers)
            return {"graph": graph, "paper_count": len(papers)}
        else:
            return {"error": f"Unknown action: {action}. Use: format, deduplicate, graph"}
    except Exception as e:
        logger.error("manage_citations failed: %s", e)
        return {"error": str(e)}
