from __future__ import annotations

import logging
import os

from ..base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class NGDCConnector(BaseConnector):
    """Connector for NGDC (National Genomics Data Center, 国家基因组科学数据中心).

    API docs: https://ngdc.cncb.ac.cn
    Provides access to GSA, GWH, OMIX, and other genomics databases.
    """

    BASE_URL = "https://ngdc.cncb.ac.cn"

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")

        base_url = os.getenv("FUSION_SCI_NGDC_URL", self.BASE_URL)
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
        cache_key = f"ngdc:search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        sub_db = kwargs.get("sub_db", "gsa")
        try:
            params = {
                "q": query,
                "page": "1",
                "size": str(max_results),
            }
            resp = await self._request_with_retry(
                "GET", f"/{sub_db}/api/search", params=params,
            )
            data = resp.json()
            items = self._parse_search_results(data, sub_db)
            total = data.get("total", len(items))
            result = DatabaseResult(
                source="ngdc",
                query=query,
                items=items,
                total_count=total,
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("NGDC search failed: %s", e)
            return DatabaseResult(source="ngdc", query=query, error=str(e))

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        cache_key = f"ngdc:fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        sub_db = kwargs.get("sub_db", "gsa")
        try:
            resp = await self._request_with_retry(
                "GET", f"/{sub_db}/api/detail/{identifier}",
            )
            data = resp.json()
            item = self._parse_detail(data, sub_db)
            result = DatabaseResult(
                source="ngdc",
                query=identifier,
                items=[item] if item else [],
                total_count=1 if item else 0,
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("NGDC fetch failed: %s", e)
            return DatabaseResult(source="ngdc", query=identifier, error=str(e))

    async def search_gsa(self, query: str, max_results: int = 20) -> DatabaseResult:
        return await self.search(query, max_results=max_results, sub_db="gsa")

    async def search_gwh(self, query: str, max_results: int = 20) -> DatabaseResult:
        return await self.search(query, max_results=max_results, sub_db="gwh")

    async def search_omix(self, query: str, max_results: int = 20) -> DatabaseResult:
        return await self.search(query, max_results=max_results, sub_db="omix")

    def _parse_search_results(self, data: dict, sub_db: str) -> list[dict]:
        results = data.get("results", data.get("items", data.get("data", [])))
        if not isinstance(results, list):
            return []
        items = []
        for entry in results[:50]:
            item = {
                "source": "ngdc",
                "sub_database": sub_db,
                "accession": self.safe_text(entry.get("accession", entry.get("id", ""))),
                "title": self.safe_text(entry.get("title", entry.get("name", ""))),
                "description": self.safe_text(entry.get("description", entry.get("abstract", ""))),
                "organism": self.safe_text(entry.get("organism", entry.get("species", ""))),
                "submitter": self.safe_text(entry.get("submitter", entry.get("creator", ""))),
                "release_date": self.safe_text(entry.get("release_date", entry.get("create_time", ""))),
                "url": self.safe_text(entry.get("url", "")),
            }
            items.append(item)
        return items

    def _parse_detail(self, data: dict, sub_db: str) -> dict | None:
        if not data:
            return None
        return {
            "source": "ngdc",
            "sub_database": sub_db,
            "accession": self.safe_text(data.get("accession", data.get("id", ""))),
            "title": self.safe_text(data.get("title", data.get("name", ""))),
            "description": self.safe_text(data.get("description", data.get("abstract", ""))),
            "organism": self.safe_text(data.get("organism", data.get("species", ""))),
            "submitter": self.safe_text(data.get("submitter", data.get("creator", ""))),
            "release_date": self.safe_text(data.get("release_date", data.get("create_time", ""))),
            "data_type": self.safe_text(data.get("data_type", "")),
            "sample_count": data.get("sample_count", data.get("sample_number", "")),
            "url": self.safe_text(data.get("url", "")),
        }
