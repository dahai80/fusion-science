"""Literature reader — LLM-driven deep reading of academic papers.

Provides section-by-section summarization, key finding extraction,
TLDR one-liner generation, and methodological assessment.
All reading uses LLMGateway for inference.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.gateway import LLMGateway

logger = logging.getLogger(__name__)


@dataclass
class SectionSummary:
    section_name: str
    summary: str
    key_points: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class PaperReading:
    paper_id: str
    title: str = ""
    tldr: str = ""
    overall_summary: str = ""
    section_summaries: list[SectionSummary] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    methodology_assessment: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    reading_quality: float = 0.0

    def to_dict(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "tldr": self.tldr,
            "overall_summary": self.overall_summary,
            "section_summaries": [
                {"section_name": s.section_name, "summary": s.summary, "key_points": s.key_points}
                for s in self.section_summaries
            ],
            "key_findings": self.key_findings,
            "methodology_assessment": self.methodology_assessment,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "reading_quality": self.reading_quality,
        }


_READ_PROMPT = (
    "You are a scientific research assistant performing a deep reading of an academic paper. "
    "Be precise, rigorous, and evidence-based. Extract concrete facts, not vague statements."
)

_TLDR_PROMPT = (
    "Summarize this paper in ONE concise sentence (max 30 words). "
    "Focus on: what was studied, key method, main result. "
    "Format: [Method] study of [Topic] found [Key Result]."
)

_SUMMARY_PROMPT = (
    "Provide a structured summary of this paper section. Include:\n"
    "1. Main content (2-3 sentences)\n"
    "2. Key points (bullet list, max 5)\n"
    "3. Confidence in understanding (0.0-1.0)\n\n"
    "Section: {section_name}\n"
    "Content:\n{content}"
)

_OVERALL_PROMPT = (
    "Based on the following section summaries, provide:\n"
    "1. Overall summary (3-5 sentences)\n"
    "2. Key findings (numbered list, max 5)\n"
    "3. Methodology assessment (strengths and limitations)\n"
    "4. Strengths of the paper (bullet list, max 3)\n"
    "5. Weaknesses of the paper (bullet list, max 3)\n\n"
    "Section summaries:\n{summaries}"
)


class LiteratureReader:
    def __init__(self, gateway: LLMGateway | None = None):
        self._gateway = gateway

    async def read_paper(
        self,
        paper: Any,
        paper_id: str = "",
    ) -> PaperReading:
        if not self._gateway:
            logger.warning("No LLMGateway configured, returning stub reading")
            return self._stub_reading(paper, paper_id)

        pid = (
            paper_id
            or getattr(paper, "pmid", "")
            or getattr(paper, "doi", "")
            or getattr(paper, "arxiv_id", "")
            or paper.title[:40]
        )
        reading = PaperReading(paper_id=pid, title=paper.title)

        reading.tldr = await self._generate_tldr(paper)

        sections = getattr(paper, "sections", None)
        if sections:
            for section_name, content in sections.items():
                if not content.strip():
                    continue
                summary = await self._summarize_section(section_name, content)
                reading.section_summaries.append(summary)
        elif paper.abstract:
            summary = await self._summarize_section("abstract", paper.abstract)
            reading.section_summaries.append(summary)

        if reading.section_summaries:
            overall = await self._generate_overall(reading.section_summaries, paper.title)
            reading.overall_summary = overall.get("summary", "")
            reading.key_findings = overall.get("key_findings", [])
            reading.methodology_assessment = overall.get("methodology_assessment", "")
            reading.strengths = overall.get("strengths", [])
            reading.weaknesses = overall.get("weaknesses", [])
            reading.reading_quality = overall.get("quality_score", 0.5)

        logger.info("Paper reading complete: %s, findings=%d", pid, len(reading.key_findings))
        return reading

    async def read_papers(
        self,
        papers: list[Any],
        max_concurrent: int = 3,
    ) -> list[PaperReading]:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _read(paper):
            async with semaphore:
                return await self.read_paper(paper)

        results = await asyncio.gather(
            *[_read(p) for p in papers],
            return_exceptions=True,
        )

        readings = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Paper reading failed: %s", r)
                continue
            readings.append(r)
        logger.info("Read %d/%d papers", len(readings), len(papers))
        return readings

    async def _generate_tldr(self, paper: Any) -> str:
        content = paper.abstract or ""
        sections = getattr(paper, "sections", None)
        if sections:
            content = sections.get("abstract", content)
        if not content:
            return ""

        messages = [
            {"role": "system", "content": _READ_PROMPT},
            {"role": "user", "content": f"{_TLDR_PROMPT}\n\nPaper: {paper.title}\n\n{content[:3000]}"},
        ]
        resp = await self._gateway.chat(messages, temperature=0.2, max_tokens=100)
        if resp.error:
            logger.warning("TLDR generation failed: %s", resp.error)
            return ""
        return resp.content.strip()

    async def _summarize_section(self, section_name: str, content: str) -> SectionSummary:
        prompt = _SUMMARY_PROMPT.format(section_name=section_name, content=content[:4000])
        messages = [
            {"role": "system", "content": _READ_PROMPT},
            {"role": "user", "content": prompt},
        ]

        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["summary", "key_points", "confidence"],
        }

        result = await self._gateway.structured_output(messages, schema, temperature=0.2)
        if result.error or not result.parsed:
            logger.warning("Section summary failed for %s: %s", section_name, result.error)
            return SectionSummary(
                section_name=section_name,
                summary=content[:200],
                key_points=[],
                confidence=0.0,
            )

        data = result.parsed
        return SectionSummary(
            section_name=section_name,
            summary=data.get("summary", ""),
            key_points=data.get("key_points", []),
            confidence=data.get("confidence", 0.0),
        )

    async def _generate_overall(
        self,
        summaries: list[SectionSummary],
        title: str,
    ) -> dict:
        summaries_text = "\n\n".join(f"[{s.section_name}] {s.summary}" for s in summaries)
        prompt = _OVERALL_PROMPT.format(summaries=summaries_text)

        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "key_findings": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "methodology_assessment": {"type": "string"},
                "strengths": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                "weaknesses": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                "quality_score": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["summary", "key_findings", "methodology_assessment", "strengths", "weaknesses"],
        }

        messages = [
            {"role": "system", "content": _READ_PROMPT},
            {"role": "user", "content": f"Paper title: {title}\n\n{prompt}"},
        ]
        result = await self._gateway.structured_output(messages, schema, temperature=0.2)
        if result.error or not result.parsed:
            logger.warning("Overall assessment failed: %s", result.error)
            return {
                "summary": "",
                "key_findings": [],
                "methodology_assessment": "",
                "strengths": [],
                "weaknesses": [],
                "quality_score": 0.3,
            }
        return result.parsed

    def _stub_reading(self, paper: Any, paper_id: str) -> PaperReading:
        pid = paper_id or getattr(paper, "pmid", "") or getattr(paper, "doi", "") or paper.title[:40]
        abstract = getattr(paper, "abstract", "")
        return PaperReading(
            paper_id=pid,
            title=paper.title,
            tldr=f"[No LLM] {paper.title[:60]}",
            overall_summary=abstract[:300] if abstract else "",
            section_summaries=[SectionSummary(section_name="abstract", summary=abstract[:200] if abstract else "")]
            if abstract
            else [],
            key_findings=[],
            methodology_assessment="LLM unavailable — no assessment",
            reading_quality=0.0,
        )
