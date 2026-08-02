"""PubMed connector — search and retrieve biomedical literature.

Uses the NCBI E-utilities API (https://eutils.ncbi.nlm.nih.gov/entrez/eutils/)
with support for domestic mirror fallback via CNKI or China National
Science Library mirrors.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class PubMedConnector(BaseConnector):
    """Connector for PubMed biomedical literature database.

    Uses NCBI E-utilities API.
    Domestic mirror: China National Science Library mirror.
    """

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    MIRROR_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"  # PubMed无官方国内镜像，使用CNKI替代

    def __init__(
        self,
        email: str = "research@localhost",
        tool_name: str = "fusion-science",
        api_key: str = "",
        use_mirror: bool | None = None,  # None = auto-detect from env
        offline_mode: bool | None = None,  # None = auto-detect
    ):
        # Auto-detect from environment variables
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")

        mirror_url = os.getenv("FUSION_SCI_PUBMED_MIRROR", self.MIRROR_URL)
        config = ConnectorConfig(
            base_url=self.BASE_URL,
            mirror_url=mirror_url,
            use_mirror=use_mirror,
            offline_mode=offline_mode,
            timeout=30.0,
            rate_limit=0.34,  # NCBI allows 3 requests/sec without API key, 10/sec with
        )
        super().__init__(config)
        self.email = email
        self.tool_name = tool_name
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 20, **kwargs) -> DatabaseResult:
        """Search PubMed for publications matching the query.

        Args:
            query: PubMed search query (supports MeSH terms, Boolean operators).
            max_results: Maximum number of results to return.
            **kwargs: Additional search parameters (e.g., date range, sort).

        Returns:
            DatabaseResult with matched publications.
        """
        cache_key = f"search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(max_results),
            "retmode": "json",
            "email": self.email,
            "tool": self.tool_name,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            resp = await self._request_with_retry("GET", "/esearch.fcgi", params=params)
            data = resp.json()

            id_list = data.get("esearchresult", {}).get("idlist", [])
            total_count = int(data.get("esearchresult", {}).get("count", "0"))

            items = []
            if id_list:
                items = await self._fetch_details(id_list)

            result = DatabaseResult(
                source="pubmed",
                query=query,
                items=items,
                total_count=total_count,
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("PubMed search failed: %s", e)
            return DatabaseResult(
                source="pubmed",
                query=query,
                error=str(e),
            )

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch a PubMed record by PMID.

        Args:
            identifier: PubMed ID (PMID).
            **kwargs: Additional parameters.

        Returns:
            DatabaseResult with the publication details.
        """
        cache_key = f"fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            items = await self._fetch_details([identifier])
            result = DatabaseResult(
                source="pubmed",
                query=identifier,
                items=items,
                total_count=len(items),
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("PubMed fetch failed: %s", e)
            return DatabaseResult(
                source="pubmed",
                query=identifier,
                error=str(e),
            )

    async def _fetch_details(self, pmids: list[str]) -> list[dict]:
        """Fetch full details for a list of PMIDs.

        Args:
            pmids: List of PubMed IDs.

        Returns:
            List of publication detail dicts.
        """
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "email": self.email,
            "tool": self.tool_name,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        resp = await self._request_with_retry("GET", "/efetch.fcgi", params=params)
        return self._parse_publications(resp.text)

    def _parse_publications(self, xml_text: str) -> list[dict]:
        """Parse PubMed XML response into structured publication dicts.

        Args:
            xml_text: Raw XML response from NCBI.

        Returns:
            List of parsed publication dicts.
        """
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            articles = []
            for article in root.findall(".//PubmedArticle"):
                pub = self._parse_single_article(article)
                if pub:
                    articles.append(pub)
            return articles
        except Exception as e:
            logger.error("Failed to parse PubMed XML: %s", e)
            return []

    def _parse_single_article(self, article: Any) -> dict[str, Any] | None:
        """Parse a single PubmedArticle XML element."""
        try:

            medline = article.find(".//MedlineCitation")
            if medline is None:
                return None

            # PMID
            pmid = self._get_text(medline, "PMID")

            # Article info
            art = medline.find(".//Article")
            if art is None:
                return None

            title = self._get_text(art, "ArticleTitle")
            abstract = self._get_text(art, "Abstract/AbstractText")

            # Authors
            authors = []
            for author in art.findall(".//Author"):
                last = self._get_text(author, "LastName")
                fore = self._get_text(author, "ForeName")
                if last or fore:
                    authors.append(f"{fore or ''} {last or ''}".strip())

            # Journal
            journal = self._get_text(art, "Journal/Title")
            iso_journal = self._get_text(art, "Journal/ISOAbbreviation")

            # Publication info
            year = self._get_text(art, "Journal/JournalIssue/PubDate/Year")
            if not year:
                year = self._get_text(art, "Journal/JournalIssue/PubDate/MedlineDate")
            volume = self._get_text(art, "Journal/JournalIssue/Volume")
            issue = self._get_text(art, "Journal/JournalIssue/Issue")
            pages = self._get_text(art, "Pagination/MedlinePgn")

            # DOI
            doi = ""
            for eid in article.findall(".//ArticleIdList/ArticleId"):
                if eid.get("IdType") == "doi":
                    doi = eid.text or ""
                    break

            # MeSH terms
            mesh_terms = []
            for mesh in medline.findall(".//MeshHeadingList/MeshHeading"):
                desc = self._get_text(mesh, "DescriptorName")
                if desc:
                    mesh_terms.append(desc)

            # Keywords
            keywords = []
            for kw in medline.findall(".//KeywordList/Keyword"):
                if kw.text:
                    keywords.append(kw.text)

            return {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal or iso_journal,
                "year": year,
                "volume": volume,
                "issue": issue,
                "pages": pages,
                "doi": doi,
                "mesh_terms": mesh_terms,
                "keywords": keywords,
                "source": "PubMed",
            }
        except Exception as e:
            logger.warning("Failed to parse article: %s", e)
            return None

    @staticmethod
    def _get_text(parent: Any, path: str) -> str:
        """Get text from an XML element path."""
        elem = parent.find(path) if hasattr(parent, "find") else None
        return "".join(elem.itertext()) if elem is not None else ""

    async def search_by_mesh(self, mesh_term: str, max_results: int = 20) -> DatabaseResult:
        """Search PubMed by MeSH (Medical Subject Heading) term.

        Args:
            mesh_term: MeSH term (e.g., "Alzheimer Disease", "CRISPR-Cas9").
            max_results: Maximum results.

        Returns:
            DatabaseResult with matched publications.
        """
        return await self.search(f"{mesh_term}[MeSH Terms]", max_results=max_results)

    async def search_by_author(self, author: str, max_results: int = 20) -> DatabaseResult:
        """Search PubMed by author name.

        Args:
            author: Author name (e.g., "Smith J").
            max_results: Maximum results.

        Returns:
            DatabaseResult with matched publications.
        """
        return await self.search(f"{author}[Author]", max_results=max_results)

    async def search_by_gene(self, gene: str, max_results: int = 20) -> DatabaseResult:
        """Search PubMed for publications related to a gene.

        Args:
            gene: Gene symbol or name (e.g., "TP53", "BRCA1").
            max_results: Maximum results.

        Returns:
            DatabaseResult with matched publications.
        """
        return await self.search(f"{gene}[Gene] OR {gene}[Title/Abstract]", max_results=max_results)
