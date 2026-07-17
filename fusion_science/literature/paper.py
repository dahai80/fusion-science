"""Paper generation — AI-assisted scientific paper writing.

Provides tools for:
- Paper structure generation (IMRaD format)
- Section-by-section writing with citations
- Methods section generation from analysis code
- Figure legend generation
- Reference formatting in multiple styles
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .search import Paper

logger = logging.getLogger(__name__)


@dataclass
class PaperSection:
    """A section of a scientific paper."""

    heading: str
    content: str = ""
    word_count: int = 0
    citations: list[str] = field(default_factory=list)


@dataclass
class PaperDraft:
    """A complete scientific paper draft."""

    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    sections: list[PaperSection] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    figures: list[dict[str, str]] = field(default_factory=list)
    acknowledgments: str = ""
    status: str = "draft"  # draft, reviewed, final


class PaperGenerator:
    """AI-assisted scientific paper generator.

    Generates structured paper drafts following IMRaD format
    (Introduction, Methods, Results, and Discussion) with
    proper citations and formatting.
    """

    # Standard IMRaD section structure
    IMRAD_SECTIONS = [
        "Abstract",
        "Introduction",
        "Methods",
        "Results",
        "Discussion",
        "Conclusion",
    ]

    # Extended section list for comprehensive papers
    EXTENDED_SECTIONS = [
        "Abstract",
        "Introduction",
        "Background",
        "Related Work",
        "Methods",
        "Experimental Design",
        "Data Collection",
        "Statistical Analysis",
        "Results",
        "Discussion",
        "Conclusion",
        "Data Availability",
        "Code Availability",
        "Acknowledgments",
    ]

    def __init__(self, engine=None):
        self.engine = engine  # Optional LLM engine for content generation

    def create_paper(
        self,
        title: str,
        sections: list[str] | None = None,
        papers: list[Paper] | None = None,
    ) -> PaperDraft:
        """Create a new paper draft with the specified structure.

        Args:
            title: Paper title.
            sections: Section headings (default: IMRaD).
            papers: Reference papers for citations.

        Returns:
            PaperDraft with empty sections ready for writing.
        """
        sections = sections or self.IMRAD_SECTIONS
        paper = PaperDraft(title=title)

        # Create sections
        for heading in sections:
            paper.sections.append(PaperSection(heading=heading))

        # Add references
        if papers:
            for p in papers:
                ref = self._format_reference(p)
                paper.references.append(ref)

        return paper

    async def write_section(
        self,
        paper: PaperDraft,
        section_index: int,
        context: str = "",
        engine_context: dict[str, Any] | None = None,
    ) -> PaperDraft:
        """Write a section of the paper using the LLM engine.

        Args:
            paper: The paper draft.
            section_index: Index of the section to write.
            context: Additional context for writing.
            engine_context: Optional data/results for the engine.

        Returns:
            Updated PaperDraft.
        """
        if section_index >= len(paper.sections):
            return paper

        section = paper.sections[section_index]

        # If no engine, fill with placeholder content
        if not self.engine:
            section.content = self._generate_placeholder(section.heading, context)
            section.word_count = len(section.content.split())
            return paper

        # Build the prompt for the LLM
        prompt = self._build_section_prompt(
            paper.title, section.heading, context, paper.references
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            resp = await self.engine.chat(messages, temperature=0.7, max_tokens=2048)
            section.content = resp.content.strip()
            section.word_count = len(section.content.split())
        except Exception as e:
            logger.error("Failed to write section '%s': %s", section.heading, e)
            section.content = self._generate_placeholder(section.heading, context)

        return paper

    def _build_section_prompt(
        self,
        title: str,
        heading: str,
        context: str,
        references: list[str],
    ) -> str:
        """Build a prompt for the LLM to write a paper section.

        Args:
            title: Paper title.
            heading: Section heading.
            context: Additional context.
            references: List of formatted references.

        Returns:
            Prompt string.
        """
        ref_text = "\n".join(references[:20]) if references else "No references provided."

        return f"""Write the "{heading}" section of a scientific paper titled "{title}".

Context:
{context}

Available references:
{ref_text}

Requirements:
- Follow standard scientific writing conventions
- Use clear, precise language
- Cite relevant references using [1], [2], etc.
- Write in a formal academic style
- Aim for appropriate section length (300-800 words for most sections)

Write only the section content, not the heading."""

    def _generate_placeholder(self, heading: str, context: str) -> str:
        """Generate placeholder content for a section.

        Args:
            heading: Section heading.
            context: Section context.

        Returns:
            Placeholder text.
        """
        placeholders = {
            "Abstract": "This study presents [brief summary of the research]. Our findings demonstrate [key results], suggesting [main conclusion].",
            "Introduction": "The [topic] is a critical area of research. Previous studies have shown [background]. However, [gap in knowledge] remains poorly understood. Here, we [approach] to address this question.",
            "Background": "This section provides the necessary background information for understanding the study.",
            "Methods": "Detailed methods will be described here, including experimental design, data collection, and statistical analysis.",
            "Results": "Results will be presented here with appropriate figures and statistical analyses.",
            "Discussion": "The findings are discussed in the context of the existing literature.",
            "Conclusion": "In conclusion, this study provides [summary of findings], with implications for [field].",
        }
        return placeholders.get(heading, f"Content for the {heading} section will be generated here.")

    def _format_reference(self, paper: Paper, style: str = "apa") -> str:
        """Format a paper as a reference citation.

        Args:
            paper: Paper to format.
            style: Citation style.

        Returns:
            Formatted reference string.
        """
        authors = ", ".join(paper.authors[:3])
        if len(paper.authors) > 3:
            authors += " et al."

        if style == "apa":
            ref = f"{authors} ({paper.year}). {paper.title}. *{paper.journal}*."
            if paper.doi:
                ref += f" https://doi.org/{paper.doi}"
        elif style == "nature":
            ref = f"{authors}. {paper.title}. *{paper.journal}* **volume**, pages (year)."
            if paper.doi:
                ref += f" ({paper.doi})"
        else:
            ref = f"{authors} ({paper.year}) {paper.title}. *{paper.journal}*."

        return ref

    @staticmethod
    def generate_figure_legend(
        figure_type: str,
        description: str,
        statistical_info: str = "",
    ) -> str:
        """Generate a standard figure legend.

        Args:
            figure_type: Type of figure (bar chart, heatmap, etc.).
            description: Description of the figure.
            statistical_info: Statistical test results.

        Returns:
            Formatted figure legend.
        """
        parts = [f"**Figure X.** {description}"]

        if statistical_info:
            parts.append(f"Statistical analysis: {statistical_info}")

        parts.append("Error bars represent standard deviation (SD) unless otherwise indicated.")

        return "\n\n".join(parts)

    @staticmethod
    def generate_methods_from_code(code: str, language: str = "python") -> str:
        """Generate a Methods section description from analysis code.

        Args:
            code: Analysis code to describe.
            language: Programming language used.

        Returns:
            Methods section text.
        """
        import re

        # Extract key information from code
        packages = re.findall(r"(?:import|from)\s+(\w+)", code)
        unique_packages = list(set(pkg for pkg in packages if pkg not in ("os", "sys", "warnings")))

        # Detect analysis types
        analysis_types = []
        if "ttest" in code.lower() or "t.test" in code.lower():
            analysis_types.append("Student's t-tests")
        if "anova" in code.lower():
            analysis_types.append("ANOVA")
        if "regression" in code.lower() or "lm(" in code.lower():
            analysis_types.append("regression analysis")
        if "clustering" in code.lower() or "kmeans" in code.lower():
            analysis_types.append("clustering")
        if "pca" in code.lower():
            analysis_types.append("principal component analysis (PCA)")
        if "deseq" in code.lower() or "edger" in code.lower():
            analysis_types.append("differential expression analysis")

        methods = "## Methods\n\n"

        if unique_packages:
            methods += f"### Software and Packages\n\n"
            methods += f"Data analysis was performed using {language} with the following packages: "
            methods += ", ".join(sorted(unique_packages))
            methods += ".\n\n"

        if analysis_types:
            methods += "### Statistical Analysis\n\n"
            methods += f"Statistical analyses included: {', '.join(analysis_types)}.\n"
            methods += "All tests were two-sided unless otherwise specified. "
            methods += "P-values < 0.05 were considered statistically significant.\n"

        return methods

    @staticmethod
    def check_section_balance(paper: PaperDraft) -> list[str]:
        """Check if sections are appropriately balanced in length.

        Args:
            paper: The paper draft to check.

        Returns:
            List of warnings about section balance.
        """
        warnings = []
        if not paper.sections:
            return ["Paper has no sections."]

        word_counts = [s.word_count for s in paper.sections]
        avg_words = sum(word_counts) / len(word_counts) if word_counts else 0

        for section, wc in zip(paper.sections, word_counts):
            if wc == 0:
                warnings.append(f"Section '{section.heading}' is empty.")
            elif wc < avg_words * 0.3:
                warnings.append(f"Section '{section.heading}' is significantly shorter than average ({wc} vs {avg_words:.0f} words).")

        return warnings