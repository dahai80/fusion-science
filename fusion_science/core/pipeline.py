"""Scientific pipeline definitions — pre-built workflows for common research tasks.

Provides ready-to-use pipeline templates for:
- Literature review & synthesis
- Bioinformatics data analysis
- Molecular & structural analysis
- Results provenance & reporting
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .agent import PipelineResult, ScienceAgent, SciencePipeline
from .engine import ScienceEngine

logger = logging.getLogger(__name__)


@dataclass
class PipelineTemplate:
    """A reusable pipeline template with metadata."""

    name: str
    description: str
    agents: list[dict]  # [{name, system_prompt, tools}]
    pattern: str  # "sequential", "parallel", "master_worker"
    master_agent: str = ""
    worker_agents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline templates
# ---------------------------------------------------------------------------

LITERATURE_REVIEW_TEMPLATE = PipelineTemplate(
    name="literature_review",
    description="End-to-end literature review: search → analyze → synthesize → write",
    agents=[
        {
            "name": "literature_search",
            "system_prompt": "You are a literature search specialist. Search scientific databases, retrieve relevant papers, and extract key metadata (title, authors, journal, year, DOI, abstract).",
            "tools": ["search_pubmed", "search_arxiv"],
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
    description="Omics data analysis: sequence retrieval → alignment → annotation → visualization",
    agents=[
        {
            "name": "sequence_retrieval",
            "system_prompt": "You are a bioinformatics data retrieval specialist. Fetch sequences from databases (UniProt, NCBI, Ensembl) and prepare them for analysis.",
            "tools": ["fetch_uniprot", "fetch_ncbi", "fetch_ensembl"],
        },
        {
            "name": "data_analysis",
            "system_prompt": "You are a computational biologist. Perform sequence alignment, variant analysis, or expression analysis. Generate Python/R code for the analysis.",
            "tools": ["execute_python", "execute_r"],
        },
        {
            "name": "visualization",
            "system_prompt": "You are a scientific visualization expert. Create publication-quality figures from analysis results using matplotlib, seaborn, or specialized bioinformatics plotting tools.",
            "tools": ["generate_chart", "generate_heatmap"],
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
    description="Drug discovery pipeline: target identification → molecular docking → ADME prediction",
    agents=[
        {
            "name": "target_identification",
            "system_prompt": "You are a drug discovery specialist. Identify potential drug targets from literature, databases, or sequence data.",
            "tools": ["search_chembl", "search_pdb", "search_uniprot"],
        },
        {
            "name": "molecular_docking",
            "system_prompt": "You are a computational chemist. Design molecular docking experiments, analyze binding poses, and predict binding affinities.",
            "tools": ["execute_python", "visualize_molecule"],
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


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------

class PipelineFactory:
    """Creates and configures SciencePipeline instances from templates or custom specs."""

    # Registry of built-in templates
    TEMPLATES: dict[str, PipelineTemplate] = {
        "literature_review": LITERATURE_REVIEW_TEMPLATE,
        "bioinformatics_analysis": BIOINFORMATICS_PIPELINE_TEMPLATE,
        "molecular_analysis": MOLECULAR_ANALYSIS_TEMPLATE,
    }

    def __init__(self, engine: ScienceEngine):
        self.engine = engine

    def create_pipeline(self, template_name: str) -> SciencePipeline:
        """Create a pipeline from a built-in template.

        Args:
            template_name: Name of the template (literature_review, bioinformatics_analysis, etc.).

        Returns:
            Configured SciencePipeline instance.

        Raises:
            ValueError: If the template name is not found.
        """
        template = self.TEMPLATES.get(template_name)
        if not template:
            raise ValueError(
                f"Unknown template '{template_name}'. Available: {list(self.TEMPLATES.keys())}"
            )

        pipeline = SciencePipeline(self.engine)

        # Register agents
        for agent_cfg in template.agents:
            agent = ScienceAgent(
                name=agent_cfg["name"],
                engine=self.engine,
                system_prompt=agent_cfg["system_prompt"],
                tools=self._load_tools(agent_cfg.get("tools", [])),
            )
            pipeline.register_agent(agent)

        return pipeline

    def create_custom_pipeline(
        self,
        agents: list[ScienceAgent],
        pattern: str = "sequential",
    ) -> SciencePipeline:
        """Create a pipeline from custom agent definitions.

        Args:
            agents: List of ScienceAgent instances.
            pattern: Execution pattern (sequential, parallel, master_worker).

        Returns:
            Configured SciencePipeline instance.
        """
        pipeline = SciencePipeline(self.engine)
        for agent in agents:
            pipeline.register_agent(agent)
        return pipeline

    @staticmethod
    def list_templates() -> list[dict[str, Any]]:
        """List all available pipeline templates with metadata."""
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
        """Convert tool names to tool definitions for the LLM API.

        In production, this loads from a ToolRegistry.
        """
        # Placeholder tool definitions
        TOOL_DEFS = {
            "search_pubmed": {
                "type": "function",
                "function": {
                    "name": "search_pubmed",
                    "description": "Search PubMed for scientific literature",
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
            "search_arxiv": {
                "type": "function",
                "function": {
                    "name": "search_arxiv",
                    "description": "Search arXiv for preprints",
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
            "fetch_uniprot": {
                "type": "function",
                "function": {
                    "name": "fetch_uniprot",
                    "description": "Fetch protein data from UniProt",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "accession": {"type": "string", "description": "UniProt accession ID"},
                        },
                        "required": ["accession"],
                    },
                },
            },
            "fetch_ncbi": {
                "type": "function",
                "function": {
                    "name": "fetch_ncbi",
                    "description": "Fetch sequence data from NCBI",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "accession": {"type": "string", "description": "NCBI accession ID"},
                            "db": {"type": "string", "description": "Database (nucleotide, protein, etc.)"},
                        },
                        "required": ["accession"],
                    },
                },
            },
            "fetch_ensembl": {
                "type": "function",
                "function": {
                    "name": "fetch_ensembl",
                    "description": "Fetch genomic data from Ensembl",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "gene_id": {"type": "string", "description": "Ensembl gene ID"},
                            "species": {"type": "string", "description": "Species (human, mouse, etc.)"},
                        },
                        "required": ["gene_id"],
                    },
                },
            },
            "search_chembl": {
                "type": "function",
                "function": {
                    "name": "search_chembl",
                    "description": "Search ChEMBL for drug/bioactive molecule data",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query (compound name, target, etc.)"},
                        },
                        "required": ["query"],
                    },
                },
            },
            "search_pdb": {
                "type": "function",
                "function": {
                    "name": "search_pdb",
                    "description": "Search Protein Data Bank for 3D structures",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "pdb_id": {"type": "string", "description": "Optional PDB ID for direct lookup"},
                        },
                        "required": [],
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
            "execute_r": {
                "type": "function",
                "function": {
                    "name": "execute_r",
                    "description": "Execute R code for statistical analysis",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "R code to execute"},
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
            "generate_heatmap": {
                "type": "function",
                "function": {
                    "name": "generate_heatmap",
                    "description": "Generate a heatmap (e.g., for gene expression data)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "data_description": {"type": "string", "description": "Description of data for heatmap"},
                        },
                        "required": ["data_description"],
                    },
                },
            },
            "visualize_molecule": {
                "type": "function",
                "function": {
                    "name": "visualize_molecule",
                    "description": "Generate a 3D molecular structure visualization",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "smiles": {"type": "string", "description": "SMILES notation of the molecule"},
                            "pdb_id": {"type": "string", "description": "Optional PDB ID"},
                        },
                        "required": [],
                    },
                },
            },
        }

        return [TOOL_DEFS[name] for name in tool_names if name in TOOL_DEFS]