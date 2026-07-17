"""Literature review — batch analysis, comparison, and synthesis of papers.

Provides tools for:
- Batch reading and summarizing multiple papers
- Comparative analysis of methodologies and findings
- Identification of research trends and contradictions
- Structured review generation with thematic organization
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .search import Paper

logger = logging.getLogger(__name__)


@dataclass
class ReviewSection:
    """A section in a literature review."""

    title: str
    content: str
    citations: list[str] = field(default_factory=list)  # Paper identifiers


@dataclass
class LiteratureReview:
    """A complete literature review document."""

    title: str
    query: str
    sections: list[ReviewSection] = field(default_factory=list)
    papers_reviewed: list[Paper] = field(default_factory=list)
    summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


class LiteratureReviewer:
    """Analyzes and synthesizes multiple papers into a structured review.

    Features:
    - Paper clustering by topic/methodology
    - Comparative analysis of findings
    - Identification of research gaps
    - Structured review generation
    """

    def __init__(self):
        self._themes: dict[str, list[Paper]] = {}

    def analyze_papers(self, papers: list[Paper], query: str) -> LiteratureReview:
        """Analyze a set of papers and generate a structured review.

        Args:
            papers: List of papers to review.
            query: The research query.

        Returns:
            LiteratureReview with structured analysis.
        """
        review = LiteratureReview(
            title=f"Literature Review: {query}",
            query=query,
            papers_reviewed=papers,
        )

        if not papers:
            review.summary = self._generate_summary(review)
            return review

        # Cluster papers by themes
        self._cluster_by_theme(papers)

        # Generate key findings
        review.key_findings = self._extract_key_findings(papers)

        # Identify contradictions
        review.contradictions = self._identify_contradictions(papers)

        # Identify research gaps
        review.gaps = self._identify_gaps(papers, query)

        # Generate sections
        review.sections = self._generate_sections()

        # Generate summary
        review.summary = self._generate_summary(review)

        return review

    def _cluster_by_theme(self, papers: list[Paper]) -> None:
        """Cluster papers into themes based on keywords and MeSH terms."""
        self._themes = {}

        for paper in papers:
            keywords = [k.lower() for k in paper.keywords + paper.mesh_terms]
            title_lower = paper.title.lower()

            assigned = False
            for theme_keywords, theme_name in self._get_theme_keywords().items():
                if any(kw in theme_keywords or any(t in title_lower for t in theme_keywords) for kw in keywords):
                    if theme_name not in self._themes:
                        self._themes[theme_name] = []
                    self._themes[theme_name].append(paper)
                    assigned = True

            if not assigned:
                # Put in a general category
                if "General" not in self._themes:
                    self._themes["General"] = []
                self._themes["General"].append(paper)

    def _get_theme_keywords(self) -> dict[tuple[str, ...], str]:
        """Get keyword-to-theme mappings."""
        return {
            ("methodology", "method", "approach", "protocol", "pipeline", "workflow"): "Methodological Approaches",
            ("clinical", "trial", "patient", "treatment", "therapy", "therapeutic"): "Clinical Applications",
            ("genome", "genomic", "gene", "genetic", "dna", "rna", "transcript"): "Genomics & Genetics",
            ("protein", "proteom", "structure", "binding", "domain", "fold"): "Protein Structure & Function",
            ("drug", "pharma", "compound", "inhibitor", "screening", "molecular docking"): "Drug Discovery",
            ("machine learning", "deep learning", "ai", "neural", "artificial intelligence"): "AI & Machine Learning",
            ("single cell", "scRNA", "single-cell", "transcriptomic"): "Single Cell Biology",
            ("crispr", "gene editing", "genome editing", "cas9"): "Gene Editing",
            ("imaging", "microscopy", "mri", "ct", "pet", "radiology"): "Bioimaging",
            ("evolution", "phylogen", "evolutionary", "conservation"): "Evolution & Phylogenetics",
        }

    def _extract_key_findings(self, papers: list[Paper]) -> list[str]:
        """Extract key findings from the paper set.

        In a real implementation, this would use the LLM to analyze
        paper abstracts. For now, provides structured extraction.
        """
        findings = []
        # Collect unique study types
        study_types = set()
        for paper in papers:
            abstract = paper.abstract.lower()
            if "clinical trial" in abstract:
                study_types.add("Clinical trials")
            if "meta-analysis" in abstract:
                study_types.add("Meta-analyses")
            if "review" in abstract:
                study_types.add("Review articles")

        if study_types:
            findings.append(f"Analysis includes {', '.join(study_types)}")

        # Temporal distribution
        years = [p.year for p in papers if p.year]
        if years:
            findings.append(f"Papers span {min(years)}–{max(years)}")

        return findings

    def _identify_contradictions(self, papers: list[Paper]) -> list[str]:
        """Identify contradictory findings across papers.

        In a real implementation, this would use NLP/LLM to detect
        contradictions. For now, provides a structured approach.
        """
        contradictions = []
        # Group papers by topic and look for conflicting terms
        topic_groups: dict[str, list[Paper]] = {}
        for paper in papers:
            for keyword in paper.keywords + paper.mesh_terms:
                if keyword not in topic_groups:
                    topic_groups[keyword] = []
                topic_groups[keyword].append(paper)

        # Check for conflicting conclusions (simplified)
        for topic, group in topic_groups.items():
            if len(group) >= 3:
                contradictions.append(
                    f"Multiple perspectives found on '{topic}' ({len(group)} papers) — "
                    "requires detailed comparison of methodologies and conclusions"
                )

        return contradictions

    def _identify_gaps(self, papers: list[Paper], query: str) -> list[str]:
        """Identify research gaps in the literature.

        Args:
            papers: Papers to analyze.
            query: Original search query.

        Returns:
            List of identified research gaps.
        """
        gaps = []
        # Check for common limitations
        limitation_count = 0
        for paper in papers:
            abstract = paper.abstract.lower()
            if any(term in abstract for term in ["further research", "limitation", "need more", "insufficient", "warranted"]):
                limitation_count += 1

        if limitation_count > 0:
            gaps.append(f"{limitation_count} papers mention limitations or need for further research")

        # Check for missing temporal coverage
        years = [int(p.year) for p in papers if p.year and p.year.isdigit()]
        if years:
            recent = [y for y in years if y >= 2024]
            if not recent:
                gaps.append("Limited recent publications (2024+) — research may be evolving rapidly")

        return gaps

    def _generate_sections(self) -> list[ReviewSection]:
        """Generate structured review sections from clustered themes."""
        sections = []

        # Introduction
        sections.append(ReviewSection(
            title="Introduction",
            content="Overview of the research landscape and objectives of this review.",
            citations=[],
        ))

        # Thematic sections
        for theme, papers in sorted(self._themes.items(), key=lambda x: len(x[1]), reverse=True):
            citations = [p.pmid or p.doi or p.arxiv_id for p in papers if p.pmid or p.doi or p.arxiv_id]
            content = (
                f"Analysis of {len(papers)} papers on {theme}. "
                f"Key contributions include: " +
                "; ".join(p.title for p in papers[:5])
            )
            sections.append(ReviewSection(
                title=theme,
                content=content,
                citations=citations,
            ))

        # Discussion
        sections.append(ReviewSection(
            title="Discussion & Future Directions",
            content="Summary of key findings, contradictions, and gaps identified in the literature.",
            citations=[],
        ))

        return sections

    def _generate_summary(self, review: LiteratureReview) -> str:
        """Generate an executive summary of the review."""
        total = len(review.papers_reviewed)
        if total == 0:
            return "No papers were included in this review."

        sections = len(review.sections)
        themes = len(self._themes)

        return (
            f"This review analyzes {total} papers across {themes} thematic areas "
            f"organized into {sections} sections. "
            f"Key findings include {len(review.key_findings)} major observations. "
            f"{len(review.contradictions)} areas of conflicting evidence were identified, "
            f"and {len(review.gaps)} research gaps were noted."
        )

    @staticmethod
    def generate_bibliography(papers: list[Paper], style: str = "apa") -> str:
        """Generate a formatted bibliography from a list of papers.

        Args:
            papers: List of papers to cite.
            style: Citation style (apa, vancouver, etc.).

        Returns:
            Formatted bibliography string.
        """
        refs = []
        for i, paper in enumerate(papers, 1):
            authors = ", ".join(paper.authors[:3])
            if len(paper.authors) > 3:
                authors += " et al."

            if style == "apa":
                ref = f"{authors} ({paper.year}). {paper.title}. *{paper.journal}*."
                if paper.doi:
                    ref += f" https://doi.org/{paper.doi}"
            elif style == "vancouver":
                ref = f"{i}. {authors}. {paper.title}. {paper.journal}. {paper.year}"
                if paper.doi:
                    ref += f" doi:{paper.doi}"
            else:
                ref = f"[{i}] {paper.title} — {authors} ({paper.year})"

            refs.append(ref)

        return "\n\n".join(refs)