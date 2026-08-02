from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .agent import ScienceAgent, SciencePipeline
from .engine import ScienceEngine
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class PipelineTemplate:
    name: str
    description: str
    agents: list[dict]
    pattern: str
    master_agent: str = ""
    worker_agents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


LITERATURE_REVIEW_TEMPLATE = PipelineTemplate(
    name="literature_review",
    description="End-to-end literature review: search -> analyze -> synthesize -> write",
    agents=[
        {
            "name": "literature_search",
            "system_prompt": "You are a literature search specialist. Search scientific databases, retrieve relevant papers, and extract key metadata (title, authors, journal, year, DOI, abstract).",
            "tools": ["search_literature", "search_database"],
        },
        {
            "name": "literature_analysis",
            "system_prompt": "You are a research analyst. Compare and contrast findings across papers, identify methodological strengths/weaknesses, and highlight contradictions in the literature.",
            "tools": [],
        },
        {
            "name": "literature_summary",
            "system_prompt": "You are a scientific writer. Synthesize the analyzed literature into a coherent review with proper citations, organized by themes or research questions.",
            "tools": [],
        },
    ],
    pattern="sequential",
    tags=["literature", "review", "paper"],
)

BIOINFORMATICS_PIPELINE_TEMPLATE = PipelineTemplate(
    name="bioinformatics_analysis",
    description="Omics data analysis: sequence retrieval -> alignment -> annotation -> visualization",
    agents=[
        {
            "name": "sequence_retrieval",
            "system_prompt": "You are a bioinformatics data retrieval specialist. Fetch sequences from databases (UniProt, NCBI, Ensembl) and prepare them for analysis.",
            "tools": ["search_database"],
        },
        {
            "name": "data_analysis",
            "system_prompt": "You are a computational biologist. Perform sequence alignment, variant analysis, or expression analysis. Generate Python/R code for the analysis.",
            "tools": ["execute_python"],
        },
        {
            "name": "visualization",
            "system_prompt": "You are a scientific visualization expert. Create publication-quality figures from analysis results using matplotlib, seaborn, or specialized bioinformatics plotting tools.",
            "tools": ["generate_chart"],
        },
        {
            "name": "interpretation",
            "system_prompt": "You are a senior biologist. Interpret the analysis results in the context of the original research question, highlighting biological significance and limitations.",
            "tools": [],
        },
    ],
    pattern="sequential",
    tags=["bioinformatics", "omics", "sequence", "genomics"],
)

MOLECULAR_ANALYSIS_TEMPLATE = PipelineTemplate(
    name="molecular_analysis",
    description="Drug discovery pipeline: target identification -> molecular docking -> ADME prediction",
    agents=[
        {
            "name": "target_identification",
            "system_prompt": "You are a drug discovery specialist. Identify potential drug targets from literature, databases, or sequence data.",
            "tools": ["search_database", "search_literature"],
        },
        {
            "name": "molecular_docking",
            "system_prompt": "You are a computational chemist. Design molecular docking experiments, analyze binding poses, and predict binding affinities.",
            "tools": ["execute_python", "generate_chart"],
        },
        {
            "name": "adme_prediction",
            "system_prompt": "You are a pharmacokinetics specialist. Predict ADME (Absorption, Distribution, Metabolism, Excretion) properties of drug candidates.",
            "tools": [],
        },
    ],
    pattern="sequential",
    tags=["drug-discovery", "molecular", "chemistry", "pharma"],
)


_FALLBACK_TOOL_DEFS = {
    "search_literature": {
        "type": "function",
        "function": {
            "name": "search_literature",
            "description": "Search scientific literature databases (PubMed, arXiv)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results"},
                },
                "required": ["query"],
            },
        },
    },
    "search_database": {
        "type": "function",
        "function": {
            "name": "search_database",
            "description": "Search scientific databases (UniProt, PDB, Ensembl, ChEMBL)",
            "parameters": {
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name (uniprot, pdb, ensembl, chembl)"},
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["db", "query"],
            },
        },
    },
    "execute_python": {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "Execute Python code for data analysis",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
        },
    },
    "generate_chart": {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": "Generate a publication-quality chart from data",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string", "description": "Chart type (bar, line, scatter, box, violin, etc.)"},
                    "data_description": {"type": "string", "description": "Description of data to visualize"},
                },
                "required": ["chart_type", "data_description"],
            },
        },
    },
    "fetch_paper": {
        "type": "function",
        "function": {
            "name": "fetch_paper",
            "description": "Fetch full paper details by DOI or identifier",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "DOI or paper identifier"},
                },
                "required": ["identifier"],
            },
        },
    },
}


class PipelineFactory:
    TEMPLATES: dict[str, PipelineTemplate] = {
        "literature_review": LITERATURE_REVIEW_TEMPLATE,
        "bioinformatics_analysis": BIOINFORMATICS_PIPELINE_TEMPLATE,
        "molecular_analysis": MOLECULAR_ANALYSIS_TEMPLATE,
    }

    def __init__(self, engine: ScienceEngine, tool_registry: ToolRegistry | None = None):
        self.engine = engine
        self.tool_registry = tool_registry

    def create_pipeline(self, template_name: str) -> SciencePipeline:
        template = self.TEMPLATES.get(template_name)
        if not template:
            raise ValueError(
                f"Unknown template '{template_name}'. Available: {list(self.TEMPLATES.keys())}"
            )

        pipeline = SciencePipeline(self.engine, tool_registry=self.tool_registry)

        for agent_cfg in template.agents:
            agent = ScienceAgent(
                name=agent_cfg["name"],
                engine=self.engine,
                system_prompt=agent_cfg["system_prompt"],
                tools=self._load_tools(agent_cfg.get("tools", [])),
                tool_registry=self.tool_registry,
            )
            pipeline.register_agent(agent)

        return pipeline

    def create_custom_pipeline(
        self,
        agents: list[ScienceAgent],
        pattern: str = "sequential",
    ) -> SciencePipeline:
        pipeline = SciencePipeline(self.engine, tool_registry=self.tool_registry)
        for agent in agents:
            pipeline.register_agent(agent)
        return pipeline

    @staticmethod
    def list_templates() -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "pattern": t.pattern,
                "agent_count": len(t.agents),
                "tags": t.tags,
            }
            for t in PipelineFactory.TEMPLATES.values()
        ]

    def _load_tools(self, tool_names: list[str]) -> list[dict]:
        if self.tool_registry:
            openai_tools = self.tool_registry.get_openai_tools()
            registry_map = {t["function"]["name"]: t for t in openai_tools}
            result = []
            for name in tool_names:
                if name in registry_map:
                    result.append(registry_map[name])
                elif name in _FALLBACK_TOOL_DEFS:
                    result.append(_FALLBACK_TOOL_DEFS[name])
                    logger.debug("Tool '%s' not in registry, using fallback def", name)
            return result

        return [_FALLBACK_TOOL_DEFS[name] for name in tool_names if name in _FALLBACK_TOOL_DEFS]
