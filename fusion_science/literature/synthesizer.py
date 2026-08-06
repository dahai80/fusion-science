"""Literature synthesizer — multi-paper synthesis and consensus analysis.

Provides consensus scoring, contradiction detection, research gap identification,
and trend analysis across multiple papers. Uses LLMGateway for deep synthesis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.gateway import LLMGateway
from .extractor import LiteratureExtractor, StructuredExtraction

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    statement: str
    supporting_papers: list[str] = field(default_factory=list)
    contradicting_papers: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class Contradiction:
    topic: str
    position_a: str
    position_b: str
    position_a_papers: list[str] = field(default_factory=list)
    position_b_papers: list[str] = field(default_factory=list)
    possible_reason: str = ""


@dataclass
class ConsensusAnalysis:
    topic: str
    total_papers: int = 0
    supporting: int = 0
    contradicting: int = 0
    inconclusive: int = 0
    consensus_score: float = 0.0
    key_findings: list[Finding] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    research_gaps: list[str] = field(default_factory=list)
    trends: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "total_papers": self.total_papers,
            "supporting": self.supporting,
            "contradicting": self.contradicting,
            "inconclusive": self.inconclusive,
            "consensus_score": self.consensus_score,
            "key_findings": [
                {
                    "statement": f.statement,
                    "supporting": f.supporting_papers,
                    "contradicting": f.contradicting_papers,
                    "confidence": f.confidence,
                }
                for f in self.key_findings
            ],
            "contradictions": [
                {
                    "topic": c.topic,
                    "position_a": c.position_a,
                    "position_b": c.position_b,
                    "possible_reason": c.possible_reason,
                }
                for c in self.contradictions
            ],
            "research_gaps": self.research_gaps,
            "trends": self.trends,
        }


_SYNTHESIS_PROMPT = (
    "You are a scientific research assistant performing multi-paper synthesis. "
    "Analyze the extracted information from multiple papers to identify consensus, "
    "contradictions, and research gaps. Be rigorous and evidence-based."
)

_CONSENSUS_SCHEMA = {
    "type": "object",
    "properties": {
        "key_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "supporting_papers": {"type": "array", "items": {"type": "string"}},
                    "contradicting_papers": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["statement", "supporting_papers", "confidence"],
            },
            "maxItems": 10,
        },
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "position_a": {"type": "string"},
                    "position_a_papers": {"type": "array", "items": {"type": "string"}},
                    "position_b": {"type": "string"},
                    "position_b_papers": {"type": "array", "items": {"type": "string"}},
                    "possible_reason": {"type": "string"},
                },
                "required": ["topic", "position_a", "position_b"],
            },
            "maxItems": 5,
        },
        "research_gaps": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "trends": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "supporting_count": {"type": "integer"},
        "contradicting_count": {"type": "integer"},
        "inconclusive_count": {"type": "integer"},
    },
    "required": ["key_findings", "contradictions", "research_gaps"],
}


class LiteratureSynthesizer:
    def __init__(
        self,
        gateway: LLMGateway | None = None,
        extractor: LiteratureExtractor | None = None,
    ):
        self._gateway = gateway
        self._extractor = extractor or LiteratureExtractor(gateway=gateway)

    async def synthesize(
        self,
        papers: list[Any],
        topic: str = "",
        extractions: list[StructuredExtraction] | None = None,
    ) -> ConsensusAnalysis:
        if not papers:
            return ConsensusAnalysis(topic=topic or "empty")

        topic = topic or papers[0].title[:60]

        if extractions is None:
            extractions = await self._extractor.extract_batch(papers)

        if not self._gateway:
            logger.warning("No LLMGateway, using rule-based synthesis")
            return self._rule_based_synthesize(papers, extractions, topic)

        papers_text = self._build_papers_summary(papers, extractions)

        messages = [
            {"role": "system", "content": _SYNTHESIS_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Synthesize the following {len(papers)} papers on topic: {topic}\n\nPapers:\n{papers_text[:6000]}"
                ),
            },
        ]

        result = await self._gateway.structured_output(
            messages,
            _CONSENSUS_SCHEMA,
            temperature=0.2,
        )
        if result.error or not result.parsed:
            logger.warning("LLM synthesis failed: %s", result.error)
            return self._rule_based_synthesize(papers, extractions, topic)

        data = result.parsed
        key_findings = []
        for f in data.get("key_findings", []):
            key_findings.append(
                Finding(
                    statement=f.get("statement", ""),
                    supporting_papers=f.get("supporting_papers", []),
                    contradicting_papers=f.get("contradicting_papers", []),
                    confidence=f.get("confidence", 0.5),
                )
            )

        contradictions = []
        for c in data.get("contradictions", []):
            contradictions.append(
                Contradiction(
                    topic=c.get("topic", ""),
                    position_a=c.get("position_a", ""),
                    position_a_papers=c.get("position_a_papers", []),
                    position_b=c.get("position_b", ""),
                    position_b_papers=c.get("position_b_papers", []),
                    possible_reason=c.get("possible_reason", ""),
                )
            )

        supporting = data.get("supporting_count", 0)
        contradicting = data.get("contradicting_count", 0)
        inconclusive = data.get("inconclusive_count", 0)

        if supporting + contradicting + inconclusive == 0:
            total = len(papers)
            supporting = sum(1 for f in key_findings if f.confidence >= 0.7)
            contradicting = len(contradictions)
            inconclusive = max(0, total - supporting - contradicting)

        total = supporting + contradicting + inconclusive
        if total > 0:
            consensus_score = (supporting - contradicting) / total
        else:
            consensus_score = 0.0

        analysis = ConsensusAnalysis(
            topic=topic,
            total_papers=len(papers),
            supporting=supporting,
            contradicting=contradicting,
            inconclusive=inconclusive,
            consensus_score=round(consensus_score, 3),
            key_findings=key_findings,
            contradictions=contradictions,
            research_gaps=data.get("research_gaps", []),
            trends=data.get("trends", []),
        )
        logger.info(
            "Synthesis complete: %d papers, score=%.2f, findings=%d, contradictions=%d",
            len(papers),
            analysis.consensus_score,
            len(key_findings),
            len(contradictions),
        )
        return analysis

    def _rule_based_synthesize(
        self,
        papers: list[Any],
        extractions: list[StructuredExtraction],
        topic: str,
    ) -> ConsensusAnalysis:
        study_types: dict[str, int] = {}
        all_keywords: list[str] = []
        years = []

        for paper in papers:
            abstract = getattr(paper, "abstract", "").lower()
            for st in ["rct", "cohort", "meta-analysis", "review", "case-control"]:
                if st in abstract:
                    study_types[st] = study_types.get(st, 0) + 1
            all_keywords.extend(getattr(paper, "keywords", []))
            year = getattr(paper, "year", "")
            if year and year.isdigit():
                years.append(int(year))

        gaps = []
        if extractions:
            total_limitations = sum(len(e.limitations) for e in extractions)
            if total_limitations > 0:
                gaps.append(f"{total_limitations} limitations noted across papers")

        if years:
            recent = [y for y in years if y >= 2024]
            if not recent:
                gaps.append("No recent publications (2024+) — research may be evolving")

        trends = []
        if years:
            trends.append(f"Publication years span {min(years)}–{max(years)}")
        if study_types:
            top_type = max(study_types, key=study_types.get)
            trends.append(f"Most common study type: {top_type}")

        findings = []
        seen_keywords: dict[str, int] = {}
        for kw in all_keywords:
            kw_lower = kw.lower()
            seen_keywords[kw_lower] = seen_keywords.get(kw_lower, 0) + 1
        for kw, count in sorted(seen_keywords.items(), key=lambda x: -x[1])[:5]:
            if count >= 2:
                findings.append(
                    Finding(
                        statement=f"'{kw}' appears in {count} papers",
                        supporting_papers=[],
                        confidence=min(count / len(papers), 1.0),
                    )
                )

        total = len(papers)
        supporting = sum(1 for f in findings if f.confidence >= 0.5)
        contradicting = 0
        inconclusive = max(0, total - supporting)
        score = (supporting - contradicting) / total if total > 0 else 0.0

        return ConsensusAnalysis(
            topic=topic,
            total_papers=total,
            supporting=supporting,
            contradicting=contradicting,
            inconclusive=inconclusive,
            consensus_score=round(score, 3),
            key_findings=findings,
            contradictions=[],
            research_gaps=gaps,
            trends=trends,
        )

    def _build_papers_summary(
        self,
        papers: list[Any],
        extractions: list[StructuredExtraction],
    ) -> str:
        parts = []
        for i, paper in enumerate(papers):
            ext = extractions[i] if i < len(extractions) else None
            pid = getattr(paper, "pmid", "") or getattr(paper, "doi", "") or paper.title[:30]
            section = f"[Paper {i + 1}] {pid}: {paper.title}"
            if paper.abstract:
                section += f"\nAbstract: {paper.abstract[:500]}"
            if ext and ext.study_type:
                section += f"\nStudy type: {ext.study_type}, n={ext.sample_size}"
                if ext.pico.population:
                    section += f"\nPICO: Pop={ext.pico.population[:100]}"
            parts.append(section)
        return "\n\n".join(parts)
