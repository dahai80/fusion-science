"""Literature review — LLM-driven structured review generation.

Analyzes and synthesizes multiple papers into a structured review with
PRISMA 2020 compliance. Uses LLMGateway for review generation with
fallback to rule-based analysis when LLM is unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..core.gateway import LLMGateway
from .extractor import LiteratureExtractor
from .search import Paper, PRISMAFlow, SearchPreset
from .synthesizer import ConsensusAnalysis, LiteratureSynthesizer

logger = logging.getLogger(__name__)


@dataclass
class ReviewSection:
    title: str
    content: str
    citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"title": self.title, "content": self.content, "citations": self.citations}


@dataclass
class LiteratureReview:
    title: str
    query: str
    sections: list[ReviewSection] = field(default_factory=list)
    papers_reviewed: list[Paper] = field(default_factory=list)
    summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    prisma: PRISMAFlow | None = None
    consensus: ConsensusAnalysis | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "query": self.query,
            "sections": [s.to_dict() for s in self.sections],
            "papers_reviewed": len(self.papers_reviewed),
            "summary": self.summary,
            "key_findings": self.key_findings,
            "contradictions": self.contradictions,
            "gaps": self.gaps,
            "prisma": self.prisma.to_dict() if self.prisma else None,
            "consensus": self.consensus.to_dict() if self.consensus else None,
        }


_REVIEW_PROMPT = (
    "You are a scientific research assistant generating a structured literature review. "
    "Follow PRISMA 2020 guidelines where applicable. Write in academic style with "
    "proper citations. Be objective and evidence-based."
)

_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["content"],
}


class LiteratureReviewer:
    def __init__(
        self,
        gateway: LLMGateway | None = None,
        extractor: LiteratureExtractor | None = None,
        synthesizer: LiteratureSynthesizer | None = None,
    ):
        self._gateway = gateway
        self._extractor = extractor or LiteratureExtractor(gateway=gateway)
        self._synthesizer = synthesizer or LiteratureSynthesizer(
            gateway=gateway,
            extractor=self._extractor,
        )
        self._themes: dict[str, list[Paper]] = {}

    async def analyze_papers(
        self,
        papers: list[Paper],
        query: str,
        preset: SearchPreset | None = None,
    ) -> LiteratureReview:
        review = LiteratureReview(
            title=f"Literature Review: {query}",
            query=query,
            papers_reviewed=papers,
        )

        if not papers:
            review.summary = "No papers were included in this review."
            return review

        consensus = await self._synthesizer.synthesize(papers, topic=query)
        review.consensus = consensus

        review.key_findings = [f.statement for f in consensus.key_findings]
        review.contradictions = [f"{c.topic}: {c.position_a} vs {c.position_b}" for c in consensus.contradictions]
        review.gaps = consensus.research_gaps

        if self._gateway:
            review.sections = await self._generate_sections_llm(papers, query, consensus)
        else:
            self._cluster_by_theme(papers)
            review.sections = self._generate_sections_rule(papers, query, consensus)

        review.summary = self._generate_summary(review)

        logger.info(
            "Review complete: %d papers, %d sections, consensus=%.2f",
            len(papers),
            len(review.sections),
            consensus.consensus_score,
        )
        return review

    async def _generate_sections_llm(
        self,
        papers: list[Paper],
        query: str,
        consensus: ConsensusAnalysis,
    ) -> list[ReviewSection]:
        sections = []

        intro = await self._generate_section(
            "Introduction",
            (
                f"Write the Introduction section for a literature review on: {query}\n"
                f"Papers reviewed: {len(papers)}\n"
                f"Key context: Provide background, scope, and objectives of this review."
            ),
            papers[:5],
        )
        sections.append(ReviewSection(title="Introduction", **intro))

        methods = await self._generate_section(
            "Methods",
            (
                f"Write the Methods section for a systematic review on: {query}\n"
                f"Total papers: {len(papers)}\n"
                f"Describe the search strategy, inclusion criteria, and analysis approach."
            ),
            [],
        )
        sections.append(ReviewSection(title="Methods", **methods))

        if consensus.key_findings:
            findings_text = "\n".join(
                f"- {f.statement} (confidence: {f.confidence:.0%})" for f in consensus.key_findings
            )
            results = await self._generate_section(
                "Results",
                (
                    f"Write the Results section for a literature review on: {query}\n"
                    f"Key findings:\n{findings_text}\n"
                    f"Consensus score: {consensus.consensus_score:.2f}\n"
                    f"Synthesize these findings into coherent narrative with citations."
                ),
                papers,
            )
            sections.append(ReviewSection(title="Results", **results))

        if consensus.contradictions:
            contra_text = "\n".join(f"- {c.topic}: {c.position_a} vs {c.position_b}" for c in consensus.contradictions)
            disc = await self._generate_section(
                "Discussion",
                (
                    f"Write the Discussion section for a literature review on: {query}\n"
                    f"Contradictions:\n{contra_text}\n"
                    f"Research gaps: {', '.join(consensus.research_gaps)}\n"
                    f"Discuss implications, contradictions, and future directions."
                ),
                papers,
            )
            sections.append(ReviewSection(title="Discussion", **disc))
        else:
            disc = await self._generate_section(
                "Discussion",
                (
                    f"Write the Discussion section for a literature review on: {query}\n"
                    f"Consensus score: {consensus.consensus_score:.2f}\n"
                    f"Research gaps: {', '.join(consensus.research_gaps)}\n"
                    f"Discuss implications and future directions."
                ),
                papers,
            )
            sections.append(ReviewSection(title="Discussion", **disc))

        conclusion = await self._generate_section(
            "Conclusion",
            (
                f"Write a concise Conclusion for a literature review on: {query}\n"
                f"Papers: {len(papers)}, Consensus: {consensus.consensus_score:.2f}\n"
                f"Summarize key takeaways and recommendations."
            ),
            [],
        )
        sections.append(ReviewSection(title="Conclusion", **conclusion))

        return sections

    async def _generate_section(
        self,
        section_name: str,
        prompt: str,
        papers: list[Paper],
    ) -> dict:
        paper_context = ""
        if papers:
            paper_context = "\n\nAvailable citations:\n" + "\n".join(
                f"[{p.pmid or p.doi or p.arxiv_id or i + 1}] {p.title} ({p.year})" for i, p in enumerate(papers[:20])
            )

        messages = [
            {"role": "system", "content": _REVIEW_PROMPT},
            {"role": "user", "content": prompt + paper_context},
        ]

        result = await self._gateway.structured_output(
            messages,
            _SECTION_SCHEMA,
            temperature=0.3,
            max_tokens=2048,
        )
        if result.error or not result.parsed:
            logger.warning("Section '%s' LLM generation failed: %s", section_name, result.error)
            resp = await self._gateway.chat(messages, temperature=0.3, max_tokens=2048)
            return {"content": resp.content, "citations": []}

        data = result.parsed
        return {
            "content": data.get("content", ""),
            "citations": data.get("citations", []),
        }

    def _cluster_by_theme(self, papers: list[Paper]) -> None:
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
                if "General" not in self._themes:
                    self._themes["General"] = []
                self._themes["General"].append(paper)

    def _generate_sections_rule(
        self,
        papers: list[Paper],
        query: str,
        consensus: ConsensusAnalysis,
    ) -> list[ReviewSection]:
        sections = []

        sections.append(
            ReviewSection(
                title="Introduction",
                content=f"This review examines the research landscape on '{query}', "
                f"analyzing {len(papers)} papers from multiple sources.",
                citations=[],
            )
        )

        sections.append(
            ReviewSection(
                title="Methods",
                content=f"Systematic search identified {len(papers)} relevant papers. "
                f"Consensus score: {consensus.consensus_score:.2f}.",
                citations=[],
            )
        )

        for theme, theme_papers in sorted(self._themes.items(), key=lambda x: len(x[1]), reverse=True):
            citations = [p.pmid or p.doi or p.arxiv_id for p in theme_papers if p.pmid or p.doi or p.arxiv_id]
            content = f"Analysis of {len(theme_papers)} papers on {theme}. Key contributions: " + "; ".join(
                p.title for p in theme_papers[:5]
            )
            sections.append(ReviewSection(title=theme, content=content, citations=citations))

        if consensus.key_findings:
            findings_text = "; ".join(f.statement for f in consensus.key_findings[:5])
            sections.append(
                ReviewSection(
                    title="Key Findings",
                    content=findings_text,
                    citations=[],
                )
            )

        sections.append(
            ReviewSection(
                title="Discussion & Future Directions",
                content=(
                    f"Summary: {len(papers)} papers reviewed, "
                    f"consensus score {consensus.consensus_score:.2f}. "
                    f"Gaps: {'; '.join(consensus.research_gaps[:3]) if consensus.research_gaps else 'None identified'}."
                ),
                citations=[],
            )
        )

        return sections

    def _generate_summary(self, review: LiteratureReview) -> str:
        total = len(review.papers_reviewed)
        if total == 0:
            return "No papers were included in this review."

        sections = len(review.sections)
        themes = len(self._themes)
        score = review.consensus.consensus_score if review.consensus else 0.0

        return (
            f"This review analyzes {total} papers across {themes} thematic areas "
            f"organized into {sections} sections. "
            f"Consensus score: {score:.2f}. "
            f"{len(review.key_findings)} major findings, "
            f"{len(review.contradictions)} contradictions, "
            f"{len(review.gaps)} research gaps identified."
        )

    def _get_theme_keywords(self) -> dict[tuple[str, ...], str]:
        return {
            ("methodology", "method", "approach", "protocol", "pipeline", "workflow"): "Methodological Approaches",
            ("clinical", "trial", "patient", "treatment", "therapy", "therapeutic"): "Clinical Applications",
            ("genome", "genomic", "gene", "genetic", "dna", "rna", "transcript"): "Genomics & Genetics",
            ("protein", "proteom", "structure", "binding", "domain", "fold"): "Protein Structure & Function",
            ("drug", "pharma", "compound", "inhibitor", "screening", "molecular docking"): "Drug Discovery",
            ("machine learning", "deep learning", "ai", "neural", "artificial intelligence"): "AI & Machine Learning",
            ("single cell", "scrna", "single-cell", "transcriptomic"): "Single Cell Biology",
            ("crispr", "gene editing", "genome editing", "cas9"): "Gene Editing",
            ("imaging", "microscopy", "mri", "ct", "pet", "radiology"): "Bioimaging",
            ("evolution", "phylogen", "evolutionary", "conservation"): "Evolution & Phylogenetics",
        }

    @staticmethod
    def generate_bibliography(papers: list[Paper], style: str = "apa") -> str:
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
