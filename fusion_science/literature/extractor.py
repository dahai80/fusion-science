"""Literature extractor — structured extraction from academic papers.

Provides PICO extraction, effect size parsing, study type classification,
limitation identification, and funding source detection.
Uses LLMGateway structured_output for reliable JSON extraction.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..core.gateway import LLMGateway

logger = logging.getLogger(__name__)


@dataclass
class PICO:
    population: str = ""
    intervention: str = ""
    comparator: str = ""
    outcome: str = ""


@dataclass
class StructuredExtraction:
    study_type: str = ""
    pico: PICO = field(default_factory=PICO)
    sample_size: int = 0
    effect_size: float | None = None
    confidence_interval: tuple[float, float] | None = None
    p_value: float | None = None
    limitations: list[str] = field(default_factory=list)
    funding_source: str = ""

    def to_dict(self) -> dict:
        return {
            "study_type": self.study_type,
            "pico": {
                "population": self.pico.population,
                "intervention": self.pico.intervention,
                "comparator": self.pico.comparator,
                "outcome": self.pico.outcome,
            },
            "sample_size": self.sample_size,
            "effect_size": self.effect_size,
            "confidence_interval": list(self.confidence_interval) if self.confidence_interval else None,
            "p_value": self.p_value,
            "limitations": self.limitations,
            "funding_source": self.funding_source,
        }


_EXTRACT_PROMPT = (
    "You are a scientific research assistant extracting structured information from academic papers. "
    "Extract precise, evidence-based information. If uncertain, leave fields empty rather than guessing."
)

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "study_type": {
            "type": "string",
            "description": "One of: RCT, cohort, case_control, cross_sectional, case_report, meta_analysis, review, other",
        },
        "population": {"type": "string"},
        "intervention": {"type": "string"},
        "comparator": {"type": "string"},
        "outcome": {"type": "string"},
        "sample_size": {"type": "integer", "minimum": 0},
        "effect_size": {"type": "number"},
        "confidence_interval_lower": {"type": "number"},
        "confidence_interval_upper": {"type": "number"},
        "p_value": {"type": "number"},
        "limitations": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "funding_source": {"type": "string"},
    },
    "required": ["study_type", "population", "intervention", "comparator", "outcome"],
}


class LiteratureExtractor:
    def __init__(self, gateway: LLMGateway | None = None):
        self._gateway = gateway

    async def extract(
        self,
        paper: Any,
        paper_id: str = "",
    ) -> StructuredExtraction:
        if not self._gateway:
            logger.warning("No LLMGateway configured, using rule-based extraction")
            return self._rule_based_extract(paper, paper_id)

        pid = paper_id or getattr(paper, "pmid", "") or getattr(paper, "doi", "") or paper.title[:40]
        content = self._build_content(paper)
        if not content:
            logger.warning("No content to extract from: %s", pid)
            return StructuredExtraction()

        messages = [
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": (
                f"Extract structured information from this paper:\n\n"
                f"Title: {paper.title}\n\n"
                f"Content:\n{content[:5000]}"
            )},
        ]

        result = await self._gateway.structured_output(
            messages, _EXTRACTION_SCHEMA, temperature=0.1,
        )
        if result.error or not result.parsed:
            logger.warning("LLM extraction failed for %s: %s", pid, result.error)
            return self._rule_based_extract(paper, paper_id)

        data = result.parsed
        ci = None
        if "confidence_interval_lower" in data and "confidence_interval_upper" in data:
            try:
                lower = float(data["confidence_interval_lower"])
                upper = float(data["confidence_interval_upper"])
                ci = (lower, upper)
            except (TypeError, ValueError):
                pass

        pico = PICO(
            population=data.get("population", ""),
            intervention=data.get("intervention", ""),
            comparator=data.get("comparator", ""),
            outcome=data.get("outcome", ""),
        )

        sample_size = 0
        if data.get("sample_size"):
            with contextlib.suppress(TypeError, ValueError):
                sample_size = int(data["sample_size"])

        effect_size = None
        if data.get("effect_size") is not None:
            with contextlib.suppress(TypeError, ValueError):
                effect_size = float(data["effect_size"])

        p_value = None
        if data.get("p_value") is not None:
            with contextlib.suppress(TypeError, ValueError):
                p_value = float(data["p_value"])

        extraction = StructuredExtraction(
            study_type=data.get("study_type", ""),
            pico=pico,
            sample_size=sample_size,
            effect_size=effect_size,
            confidence_interval=ci,
            p_value=p_value,
            limitations=data.get("limitations", []),
            funding_source=data.get("funding_source", ""),
        )
        logger.info("Extracted from %s: type=%s, n=%d", pid, extraction.study_type, extraction.sample_size)
        return extraction

    async def extract_pico(self, paper: Any, paper_id: str = "") -> PICO:
        extraction = await self.extract(paper, paper_id)
        return extraction.pico

    async def extract_batch(
        self,
        papers: list[Any],
        max_concurrent: int = 3,
    ) -> list[StructuredExtraction]:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _extract(paper):
            async with semaphore:
                return await self.extract(paper)

        results = await asyncio.gather(
            *[_extract(p) for p in papers],
            return_exceptions=True,
        )
        extractions = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Batch extraction failed: %s", r)
                extractions.append(StructuredExtraction())
            else:
                extractions.append(r)
        logger.info("Extracted %d/%d papers", len(extractions), len(papers))
        return extractions

    def _rule_based_extract(self, paper: Any, paper_id: str) -> StructuredExtraction:
        abstract = getattr(paper, "abstract", "")
        text = f"{paper.title} {abstract}".lower()

        study_type = self._classify_study_type(text)
        sample_size = self._extract_sample_size(text)
        p_value = self._extract_p_value(text)

        return StructuredExtraction(
            study_type=study_type,
            pico=PICO(
                population=self._extract_population(text),
                intervention="",
                comparator="",
                outcome="",
            ),
            sample_size=sample_size,
            p_value=p_value,
            limitations=self._extract_limitations(text),
        )

    def _classify_study_type(self, text: str) -> str:
        type_patterns = [
            ("meta_analysis", ["meta-analysis", "meta analysis", "systematic review"]),
            ("RCT", ["randomized controlled trial", "randomised controlled", "rct", "double-blind", "double blind"]),
            ("cohort", ["cohort study", "prospective cohort", "retrospective cohort", "longitudinal"]),
            ("case_control", ["case-control", "case control study"]),
            ("cross_sectional", ["cross-sectional", "cross sectional"]),
            ("case_report", ["case report", "case series"]),
            ("review", ["review article", "narrative review"]),
        ]
        for study_type, patterns in type_patterns:
            for pattern in patterns:
                if pattern in text:
                    return study_type
        return "other"

    def _extract_sample_size(self, text: str) -> int:
        patterns = [
            r"n\s*=\s*(\d+)",
            r"(\d+)\s*(?:participants?|subjects?|patients?|volunteers?)",
            r"sample\s+size\s+(?:of|was|:)\s*(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return 0

    def _extract_p_value(self, text: str) -> float | None:
        match = re.search(r"p\s*[<>=]\s*0\.?\d*", text, re.IGNORECASE)
        if not match:
            return None
        try:
            num_str = re.search(r"0\.?\d*", match.group())
            if num_str:
                return float(num_str.group())
        except ValueError:
            pass
        return None

    def _extract_population(self, text: str) -> str:
        pop_patterns = [
            r"(?:patients?|subjects?|participants?)\s+(?:with|diagnosed\s+with|suffering\s+from)\s+([^.]+)",
            r"(?:in|among)\s+(\d+)\s+(?:patients?|subjects?|participants?)",
        ]
        for pattern in pop_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()[:200]
        return ""

    def _extract_limitations(self, text: str) -> list[str]:
        limitations = []
        lim_patterns = [
            r"limitation[s]?(?:\s+(?:include|are|of))?\s*[:\-]?\s*([^.]+)",
            r"(?:further research|future studies|additional studies)\s+(?:is|are)\s+(?:needed|warranted|required)\s*([^.]+)",
        ]
        for pattern in lim_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for m in matches:
                lim = m.group(1).strip()
                if lim and len(lim) > 10:
                    limitations.append(lim[:200])
        return limitations[:5]

    def _build_content(self, paper: Any) -> str:
        parts = []
        abstract = getattr(paper, "abstract", "")
        if abstract:
            parts.append(abstract)
        full_text = getattr(paper, "full_text", "")
        if full_text:
            parts.append(full_text[:4000])
        sections = getattr(paper, "sections", None)
        if sections:
            for name, content in sections.items():
                if content.strip():
                    parts.append(f"[{name}] {content[:2000]}")
        return "\n\n".join(parts)
