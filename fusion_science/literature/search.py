"""Literature search — search and retrieve academic papers from multiple sources.

Provides unified search across PubMed, arXiv, and other academic databases,
with result deduplication, relevance scoring, and metadata extraction.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Paper:
    """A single academic paper with metadata."""

    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    pmid: str = ""
    arxiv_id: str = ""
    source: str = ""  # PubMed, arXiv, etc.
    url: str = ""
    keywords: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    citations: int = 0
    pdf_url: str = ""
    relevance_score: float = 0.0


@dataclass
class SearchResult:
    """Result of a literature search."""

    query: str
    papers: list[Paper] = field(default_factory=list)
    total_count: int = 0
    sources_used: list[str] = field(default_factory=list)
    error: str = ""


class LiteratureSearch:
    """Unified literature search across multiple academic databases.

    Aggregates results from PubMed, arXiv, and other sources with
    deduplication and relevance scoring.
    """

    def __init__(self, pubmed_email: str = "research@localhost"):
        self.pubmed_email = pubmed_email

    async def search(
        self,
        query: str,
        max_results: int = 20,
        sources: list[str] | None = None,
    ) -> SearchResult:
        """Search across multiple academic databases.

        Args:
            query: Search query.
            max_results: Maximum results per source.
            sources: Sources to search (default: ["pubmed", "arxiv"]).

        Returns:
            SearchResult with deduplicated papers.
        """
        sources = sources or ["pubmed", "arxiv"]
        result = SearchResult(query=query)

        tasks = []
        if "pubmed" in sources:
            tasks.append(self._search_pubmed(query, max_results))
        if "arxiv" in sources:
            tasks.append(self._search_arxiv(query, max_results))

        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers: list[Paper] = []
        for i, sr in enumerate(search_results):
            if isinstance(sr, Exception):
                logger.warning("Search source %s failed: %s", sources[i] if i < len(sources) else "unknown", sr)
                continue
            src = sources[i] if i < len(sources) else "unknown"
            result.sources_used.append(src)
            if isinstance(sr, SearchResult):
                all_papers.extend(sr.papers)

        # Deduplicate by DOI/title
        seen_dois: set[str] = set()
        seen_titles: set[str] = set()
        deduped: list[Paper] = []
        for paper in all_papers:
            key = paper.doi.lower() if paper.doi else ""
            if not key:
                key = paper.title.lower().strip()[:50]
            if key and key not in seen_dois and key not in seen_titles:
                seen_dois.add(key)
                seen_titles.add(key)
                deduped.append(paper)

        # Sort by relevance score
        deduped.sort(key=lambda p: p.relevance_score, reverse=True)

        result.papers = deduped[:max_results]
        result.total_count = len(deduped)
        return result

    async def _search_pubmed(self, query: str, max_results: int) -> SearchResult:
        """Search PubMed via the database connector."""
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
        """Search arXiv via the arXiv API."""
        import httpx
        import xml.etree.ElementTree as ET
        import os

        # arXiv API endpoint (configurable via env var for mirror support)
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

                    # Extract arXiv ID from URL
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

    def _score_relevance(self, paper: Paper, query: str) -> float:
        """Score a paper's relevance to the query.

        Args:
            paper: The paper to score.
            query: The search query.

        Returns:
            Relevance score (0-1).
        """
        score = 0.0
        query_lower = query.lower()
        terms = query_lower.split()

        if not terms:
            return 0.0

        # Title match (highest weight)
        title_lower = paper.title.lower()
        title_matches = sum(1 for t in terms if t in title_lower)
        title_score = title_matches / len(terms) * 0.4

        # Abstract match
        abstract_lower = paper.abstract.lower()
        abstract_matches = sum(1 for t in terms if t in abstract_lower)
        abstract_score = (abstract_matches / len(terms)) * 0.3

        # Keyword/MeSH match
        all_keywords = [k.lower() for k in paper.keywords + paper.mesh_terms]
        keyword_matches = sum(1 for t in terms if any(t in kw for kw in all_keywords))
        keyword_score = (keyword_matches / len(terms)) * 0.2

        # Recency bonus (more recent = slightly higher)
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

    @staticmethod
    def extract_pmids(text: str) -> list[str]:
        """Extract PubMed IDs from text.

        Args:
            text: Text containing PMIDs.

        Returns:
            List of extracted PMIDs.
        """
        return re.findall(r"PMID:\s*(\d+)", text, re.IGNORECASE)

    @staticmethod
    def extract_dois(text: str) -> list[str]:
        """Extract DOIs from text.

        Args:
            text: Text containing DOIs.

        Returns:
            List of extracted DOIs.
        """
        return re.findall(r"10\.\d{4,}/[\w\.-]+", text)