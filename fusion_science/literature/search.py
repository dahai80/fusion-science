"""Literature search — search and retrieve academic papers from multiple sources.

Provides unified search across PubMed, arXiv, and other academic databases,
with result deduplication, relevance scoring, metadata extraction,
SearchPreset levels (quick/professional/deep), and PRISMA 2020 flow tracking.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SearchPreset(Enum):
    QUICK = "quick"
    PROFESSIONAL = "pro"
    DEEP = "deep"


@dataclass
class PRISMAFlow:
    identification: int = 0
    screening: int = 0
    excluded_after_screen: int = 0
    sought_for_retrieval: int = 0
    not_retrieved: int = 0
    assessed_for_eligibility: int = 0
    excluded_after_eligibility: int = 0
    included: int = 0
    exclusion_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "identification": self.identification,
            "screening": self.screening,
            "excluded_after_screen": self.excluded_after_screen,
            "sought_for_retrieval": self.sought_for_retrieval,
            "not_retrieved": self.not_retrieved,
            "assessed_for_eligibility": self.assessed_for_eligibility,
            "excluded_after_eligibility": self.excluded_after_eligibility,
            "included": self.included,
            "exclusion_reasons": self.exclusion_reasons,
        }


PRESET_CONFIG: dict[SearchPreset, dict[str, Any]] = {
    SearchPreset.QUICK: {
        "max_results": 10,
        "sources": ["pubmed", "arxiv"],
        "min_relevance": 0.1,
        "use_prisma": False,
    },
    SearchPreset.PROFESSIONAL: {
        "max_results": 30,
        "sources": ["pubmed", "arxiv"],
        "min_relevance": 0.05,
        "use_prisma": False,
    },
    SearchPreset.DEEP: {
        "max_results": 100,
        "sources": ["pubmed", "arxiv"],
        "min_relevance": 0.0,
        "use_prisma": True,
    },
}


@dataclass
class Paper:
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    pmid: str = ""
    arxiv_id: str = ""
    source: str = ""
    url: str = ""
    keywords: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    citations: int = 0
    pdf_url: str = ""
    relevance_score: float = 0.0
    full_text: str = ""
    sections: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "journal": self.journal,
            "year": self.year,
            "doi": self.doi,
            "pmid": self.pmid,
            "arxiv_id": self.arxiv_id,
            "source": self.source,
            "url": self.url,
            "keywords": self.keywords,
            "mesh_terms": self.mesh_terms,
            "citations": self.citations,
            "pdf_url": self.pdf_url,
            "relevance_score": self.relevance_score,
        }


@dataclass
class SearchResult:
    query: str
    papers: list[Paper] = field(default_factory=list)
    total_count: int = 0
    sources_used: list[str] = field(default_factory=list)
    error: str = ""
    prisma: PRISMAFlow | None = None
    preset: SearchPreset | None = None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "papers": [p.to_dict() for p in self.papers],
            "total_count": self.total_count,
            "sources_used": self.sources_used,
            "error": self.error,
            "prisma": self.prisma.to_dict() if self.prisma else None,
            "preset": self.preset.value if self.preset else None,
        }


class LiteratureSearch:
    def __init__(
        self,
        pubmed_email: str = "research@localhost",
        tool_registry: Any | None = None,
    ):
        self.pubmed_email = pubmed_email
        self._registry = tool_registry

    async def search(
        self,
        query: str,
        max_results: int = 20,
        sources: list[str] | None = None,
        preset: SearchPreset | None = None,
    ) -> SearchResult:
        if preset is not None:
            cfg = PRESET_CONFIG[preset]
            max_results = cfg["max_results"]
            sources = cfg["sources"]
            logger.info("Using preset=%s, max_results=%d, sources=%s", preset.value, max_results, sources)

        sources = sources or ["pubmed", "arxiv"]
        result = SearchResult(query=query, preset=preset)

        tasks = []
        task_sources = []
        if "pubmed" in sources:
            tasks.append(self._search_pubmed(query, max_results))
            task_sources.append("pubmed")
        if "arxiv" in sources:
            tasks.append(self._search_arxiv(query, max_results))
            task_sources.append("arxiv")

        if self._registry and self._registry.has_tool("search_database"):
            for db_name in ["uniprot", "pdb", "ensembl", "chembl"]:
                if db_name in sources:
                    tasks.append(self._search_via_registry(db_name, query, max_results))
                    task_sources.append(db_name)

        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers: list[Paper] = []
        for i, sr in enumerate(search_results):
            if isinstance(sr, Exception):
                src = task_sources[i] if i < len(task_sources) else "unknown"
                logger.warning("Search source %s failed: %s", src, sr)
                continue
            src = task_sources[i] if i < len(task_sources) else "unknown"
            result.sources_used.append(src)
            if isinstance(sr, SearchResult):
                all_papers.extend(sr.papers)

        min_relevance = 0.0
        if preset is not None:
            min_relevance = PRESET_CONFIG[preset]["min_relevance"]

        deduped = self._deduplicate(all_papers)

        scored = []
        for paper in deduped:
            paper.relevance_score = self._score_relevance(paper, query)
            if paper.relevance_score >= min_relevance:
                scored.append(paper)

        scored.sort(key=lambda p: p.relevance_score, reverse=True)

        if preset is not None and PRESET_CONFIG[preset]["use_prisma"]:
            result.prisma = self._build_prisma(
                identification=len(all_papers),
                after_dedup=len(deduped),
                after_screening=len(scored),
                included=min(len(scored), max_results),
            )

        result.papers = scored[:max_results]
        result.total_count = len(scored)
        return result

    async def search_preset(
        self,
        query: str,
        preset: SearchPreset = SearchPreset.PROFESSIONAL,
    ) -> SearchResult:
        return await self.search(query, preset=preset)

    async def _search_via_registry(
        self, database: str, query: str, max_results: int
    ) -> SearchResult:
        if not self._registry or not self._registry.has_tool("search_database"):
            return SearchResult(query=query, error="registry_unavailable")

        raw = await self._registry.execute("search_database", {
            "database": database,
            "query": query,
            "max_results": max_results,
        })
        if isinstance(raw, dict) and "error" in raw:
            logger.warning("Registry search_database(%s) error: %s", database, raw["error"])
            return SearchResult(query=query, error=raw["error"])

        items = raw.get("items", []) if isinstance(raw, dict) else []
        papers = []
        for item in items[:max_results]:
            paper = Paper(
                title=item.get("title", item.get("name", "")),
                authors=item.get("authors", []),
                abstract=item.get("abstract", item.get("description", "")),
                journal=item.get("journal", ""),
                year=item.get("year", ""),
                doi=item.get("doi", ""),
                source=database,
                url=item.get("url", ""),
                keywords=item.get("keywords", []),
            )
            paper.relevance_score = self._score_relevance(paper, query)
            papers.append(paper)

        return SearchResult(query=query, papers=papers, total_count=len(papers))

    async def _search_pubmed(self, query: str, max_results: int) -> SearchResult:
        from ..database.pubmed import PubMedConnector

        connector = PubMedConnector(email=self.pubmed_email)
        try:
            db_result = await connector.search(query, max_results=max_results)
            papers = []
            for item in db_result.items:
                paper = Paper(
                    title=item.get("title", ""),
                    authors=item.get("authors", []),
                    abstract=item.get("abstract", ""),
                    journal=item.get("journal", ""),
                    year=item.get("year", ""),
                    doi=item.get("doi", ""),
                    pmid=item.get("pmid", ""),
                    source="PubMed",
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{item.get('pmid', '')}" if item.get("pmid") else "",
                    keywords=item.get("keywords", []),
                    mesh_terms=item.get("mesh_terms", []),
                )
                paper.relevance_score = self._score_relevance(paper, query)
                papers.append(paper)
            return SearchResult(query=query, papers=papers, total_count=len(papers))
        finally:
            await connector.close()

    async def _search_arxiv(self, query: str, max_results: int) -> SearchResult:
        import os
        import xml.etree.ElementTree as ET

        import httpx

        arxiv_api = os.getenv("FUSION_SCI_ARXIV_MIRROR", "https://export.arxiv.org/api/query")

        papers = []
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        try:
            params = {
                "search_query": f"all:{query}",
                "max_results": str(max_results),
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(arxiv_api, params=params)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)

                for entry in root.findall("atom:entry", ns):
                    title = entry.find("atom:title", ns)
                    summary = entry.find("atom:summary", ns)
                    published = entry.find("atom:published", ns)
                    arxiv_id = entry.find("atom:id", ns)
                    pdf_link = ""

                    for link in entry.findall("atom:link", ns):
                        if link.get("title") == "pdf":
                            pdf_link = link.get("href", "")

                    authors = []
                    for author in entry.findall("atom:author", ns):
                        name = author.find("atom:name", ns)
                        if name is not None and name.text:
                            authors.append(name.text)

                    arxiv_id_str = ""
                    if arxiv_id is not None and arxiv_id.text:
                        arxiv_id_str = arxiv_id.text.split("/")[-1].split("v")[0]

                    doi = ""
                    for link in entry.findall("atom:link", ns):
                        if link.get("rel") == "alternate" and "doi.org" in (link.get("href", "")):
                            doi = link.get("href", "").split("doi.org/")[-1]

                    paper = Paper(
                        title=title.text.replace("\n", " ").strip() if title is not None else "",
                        authors=authors,
                        abstract=summary.text.replace("\n", " ").strip() if summary is not None else "",
                        year=published.text[:4] if published is not None else "",
                        doi=doi,
                        arxiv_id=arxiv_id_str,
                        source="arXiv",
                        url=f"https://arxiv.org/abs/{arxiv_id_str}" if arxiv_id_str else "",
                        pdf_url=pdf_link,
                    )
                    paper.relevance_score = self._score_relevance(paper, query)
                    papers.append(paper)

        except Exception as e:
            logger.warning("arXiv search failed: %s", e)

        return SearchResult(query=query, papers=papers, total_count=len(papers))

    def _deduplicate(self, papers: list[Paper]) -> list[Paper]:
        seen_dois: set[str] = set()
        seen_titles: set[str] = set()
        deduped: list[Paper] = []
        for paper in papers:
            key = paper.doi.lower() if paper.doi else ""
            if not key:
                key = paper.title.lower().strip()[:50]
            if key and key not in seen_dois and key not in seen_titles:
                seen_dois.add(key)
                seen_titles.add(key)
                deduped.append(paper)
        return deduped

    def _score_relevance(self, paper: Paper, query: str) -> float:
        score = 0.0
        query_lower = query.lower()
        terms = query_lower.split()

        if not terms:
            return 0.0

        title_lower = paper.title.lower()
        title_matches = sum(1 for t in terms if t in title_lower)
        title_score = title_matches / len(terms) * 0.4

        abstract_lower = paper.abstract.lower()
        abstract_matches = sum(1 for t in terms if t in abstract_lower)
        abstract_score = (abstract_matches / len(terms)) * 0.3

        all_keywords = [k.lower() for k in paper.keywords + paper.mesh_terms]
        keyword_matches = sum(1 for t in terms if any(t in kw for kw in all_keywords))
        keyword_score = (keyword_matches / len(terms)) * 0.2

        recency_bonus = 0.0
        if paper.year:
            try:
                year = int(paper.year)
                current_year = datetime.now().year
                recency_bonus = max(0, 1.0 - (current_year - year) / 20) * 0.1
            except ValueError:
                pass

        score = title_score + abstract_score + keyword_score + recency_bonus
        return min(score, 1.0)

    def _build_prisma(
        self,
        identification: int,
        after_dedup: int,
        after_screening: int,
        included: int,
    ) -> PRISMAFlow:
        return PRISMAFlow(
            identification=identification,
            screening=after_dedup,
            excluded_after_screen=identification - after_dedup,
            sought_for_retrieval=after_screening,
            not_retrieved=0,
            assessed_for_eligibility=after_screening,
            excluded_after_eligibility=after_screening - included,
            included=included,
            exclusion_reasons={
                "duplicate": identification - after_dedup,
                "low_relevance": after_dedup - after_screening,
            },
        )

    @staticmethod
    def extract_pmids(text: str) -> list[str]:
        return re.findall(r"PMID:\s*(\d+)", text, re.IGNORECASE)

    @staticmethod
    def extract_dois(text: str) -> list[str]:
        return re.findall(r"10\.\d{4,}/[\w\.-]+", text)
