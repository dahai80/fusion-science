from __future__ import annotations

import logging
import os

from ..base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class ScienceDBConnector(BaseConnector):
    """Connector for ScienceDB (科学数据银行, CAS).

    API: https://www.scidb.cn
    Open science data repository by Chinese Academy of Sciences.
    Provides searchable scientific datasets with DOI.
    """

    BASE_URL = "https://www.scidb.cn"

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")

        base_url = os.getenv("FUSION_SCI_SCIENCEDB_URL", self.BASE_URL)
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
        cache_key = f"scidb:search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            params = {
                "q": query,
                "page": "1",
                "size": str(max_results),
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
                source="scidb",
                query=query,
                items=items,
                total_count=total,
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("ScienceDB search failed: %s", e)
            return DatabaseResult(source="scidb", query=query, error=str(e))

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        cache_key = f"scidb:fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            resp = await self._request_with_retry(
                "GET",
                f"/api/dataset/{identifier}",
            )
            data = resp.json()
            item = self._parse_detail(data)
            result = DatabaseResult(
                source="scidb",
                query=identifier,
                items=[item] if item else [],
                total_count=1 if item else 0,
            )
            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error("ScienceDB fetch failed: %s", e)
            return DatabaseResult(source="scidb", query=identifier, error=str(e))

    def _parse_search_results(self, data: dict) -> list[dict]:
        results = data.get("results", data.get("items", data.get("data", [])))
        if not isinstance(results, list):
            return []
        items = []
        for entry in results[:50]:
            item = {
                "source": "scidb",
                "dataset_id": self.safe_text(entry.get("id", entry.get("datasetId", ""))),
                "title": self.safe_text(entry.get("title", entry.get("name", ""))),
                "description": self.safe_text(entry.get("description", entry.get("abstract", ""))),
                "authors": self.safe_text(entry.get("authors", entry.get("creator", ""))),
                "doi": self.safe_text(entry.get("doi", "")),
                "subject": self.safe_text(entry.get("subject", entry.get("category", ""))),
                "publish_date": self.safe_text(entry.get("publishDate", entry.get("date", ""))),
                "file_count": entry.get("fileCount", entry.get("file_count", 0)),
                "download_count": entry.get("downloadCount", entry.get("download_count", 0)),
                "view_count": entry.get("viewCount", entry.get("view_count", 0)),
                "license": self.safe_text(entry.get("license", "")),
                "url": self.safe_text(entry.get("url", "")),
            }
            items.append(item)
        return items

    def _parse_detail(self, data: dict) -> dict | None:
        if not data:
            return None
        return {
            "source": "scidb",
            "dataset_id": self.safe_text(data.get("id", data.get("datasetId", ""))),
            "title": self.safe_text(data.get("title", data.get("name", ""))),
            "description": self.safe_text(data.get("description", data.get("abstract", ""))),
            "authors": self.safe_text(data.get("authors", data.get("creator", ""))),
            "doi": self.safe_text(data.get("doi", "")),
            "subject": self.safe_text(data.get("subject", data.get("category", ""))),
            "publish_date": self.safe_text(data.get("publishDate", data.get("date", ""))),
            "file_count": data.get("fileCount", data.get("file_count", 0)),
            "download_count": data.get("downloadCount", data.get("download_count", 0)),
            "view_count": data.get("viewCount", data.get("view_count", 0)),
            "license": self.safe_text(data.get("license", "")),
            "institution": self.safe_text(data.get("institution", data.get("publisher", ""))),
            "fund": self.safe_text(data.get("fund", "")),
            "url": self.safe_text(data.get("url", "")),
        }
