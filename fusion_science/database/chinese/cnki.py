from __future__ import annotations

import logging
import os

from ..base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class CNKIConnector(BaseConnector):
    """Connector for CNKI (China National Knowledge Infrastructure, 中国知网).

    API: https://www.cnki.net
    Provides Chinese academic literature search — the primary domestic
    alternative to PubMed for Chinese-language publications.
    """

    BASE_URL = "https://www.cnki.net"

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")

        base_url = os.getenv("FUSION_SCI_CNKI_URL", self.BASE_URL)
        config = ConnectorConfig(
            base_url=base_url,
            mirror_url=base_url,
            use_mirror=use_mirror,
            offline_mode=offline_mode,
            timeout=30.0,
            rate_limit=1.0,
        )
        super().__init__(config)

    async def search(self, query: str, max_results: int = 20, **kwargs) -> DatabaseResult:
        cache_key = f"cnki:search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        search_type = kwargs.get("search_type", "academic")
        try:
            params = {
                "q": query,
                "page": "1",
                "pageSize": str(max_results),
                "type": search_type,
            }
            resp = await self._request_with_retry(
                "GET",
                "/api/search",
                params=params,
            )
            data = resp.json()
            items = self._parse_search_results(data)
            total = data.get("total", data.get("totalCount", len(items)))
            result = DatabaseResult(
                source="cnki",
                query=query,
                items=items,
                total_count=total,
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("CNKI search failed: %s", e)
            return DatabaseResult(source="cnki", query=query, error=str(e))

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        cache_key = f"cnki:fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            resp = await self._request_with_retry(
                "GET",
                f"/api/detail/{identifier}",
            )
            data = resp.json()
            item = self._parse_detail(data)
            result = DatabaseResult(
                source="cnki",
                query=identifier,
                items=[item] if item else [],
                total_count=1 if item else 0,
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("CNKI fetch failed: %s", e)
            return DatabaseResult(source="cnki", query=identifier, error=str(e))

    def _parse_search_results(self, data: dict) -> list[dict]:
        results = data.get("results", data.get("items", data.get("data", [])))
        if not isinstance(results, list):
            return []
        items = []
        for entry in results[:50]:
            item = {
                "source": "cnki",
                "doc_id": self.safe_text(entry.get("docId", entry.get("id", ""))),
                "title": self.safe_text(entry.get("title", entry.get("name", ""))),
                "authors": self.safe_text(entry.get("authors", entry.get("author", ""))),
                "journal": self.safe_text(entry.get("journal", entry.get("source", ""))),
                "publish_date": self.safe_text(entry.get("publishDate", entry.get("date", ""))),
                "keywords": self.safe_text(entry.get("keywords", "")),
                "abstract": self.safe_text(entry.get("abstract", "")),
                "doi": self.safe_text(entry.get("doi", "")),
                "citation_count": entry.get("citationCount", entry.get("citeCount", 0)),
                "download_count": entry.get("downloadCount", 0),
                "url": self.safe_text(entry.get("url", "")),
            }
            items.append(item)
        return items

    def _parse_detail(self, data: dict) -> dict | None:
        if not data:
            return None
        return {
            "source": "cnki",
            "doc_id": self.safe_text(data.get("docId", data.get("id", ""))),
            "title": self.safe_text(data.get("title", data.get("name", ""))),
            "authors": self.safe_text(data.get("authors", data.get("author", ""))),
            "journal": self.safe_text(data.get("journal", data.get("source", ""))),
            "publish_date": self.safe_text(data.get("publishDate", data.get("date", ""))),
            "keywords": self.safe_text(data.get("keywords", "")),
            "abstract": self.safe_text(data.get("abstract", "")),
            "doi": self.safe_text(data.get("doi", "")),
            "citation_count": data.get("citationCount", data.get("citeCount", 0)),
            "download_count": data.get("downloadCount", 0),
            "institution": self.safe_text(data.get("institution", data.get("affiliation", ""))),
            "fund": self.safe_text(data.get("fund", "")),
            "url": self.safe_text(data.get("url", "")),
        }
