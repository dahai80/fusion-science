"""Database aggregator — multi-database parallel query with result merging.

Provides unified search across multiple scientific databases (PubMed, UniProt,
PDB, Ensembl, ChEMBL) with parallel execution, result deduplication, and
unified ranking.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .base import BaseConnector, DatabaseResult

logger = logging.getLogger(__name__)


@dataclass
class AggregatedResult:
    query: str
    databases_used: list[str] = field(default_factory=list)
    results_by_db: dict[str, DatabaseResult] = field(default_factory=dict)
    merged_items: list[dict] = field(default_factory=list)
    total_count: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "databases_used": self.databases_used,
            "total_count": self.total_count,
            "merged_items": self.merged_items[:100],
            "errors": self.errors,
            "per_db_counts": {
                db: r.total_count for db, r in self.results_by_db.items()
            },
        }


_CONNECTOR_MAP = {
    "pubmed": ("fusion_science.database.pubmed", "PubMedConnector"),
    "uniprot": ("fusion_science.database.uniprot", "UniProtConnector"),
    "pdb": ("fusion_science.database.pdb", "PDBConnector"),
    "ensembl": ("fusion_science.database.ensembl", "EnsemblConnector"),
    "chembl": ("fusion_science.database.chembl", "ChEMBLConnector"),
}


class DatabaseAggregator:
    def __init__(
        self,
        databases: list[str] | None = None,
        max_concurrent: int = 5,
    ):
        self._databases = databases or list(_CONNECTOR_MAP.keys())
        self._max_concurrent = max_concurrent
        self._connectors: dict[str, BaseConnector] = {}

    async def search(
        self,
        query: str,
        max_results: int = 20,
        databases: list[str] | None = None,
    ) -> AggregatedResult:
        dbs = databases or self._databases
        result = AggregatedResult(query=query)

        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _search_db(db_name: str) -> tuple[str, DatabaseResult | None, str | None]:
            async with semaphore:
                try:
                    connector = await self._get_connector(db_name)
                    if connector is None:
                        return db_name, None, f"Unknown database: {db_name}"
                    db_result = await connector.search(query, max_results=max_results)
                    return db_name, db_result, None
                except Exception as e:
                    logger.warning("Search failed for %s: %s", db_name, e)
                    return db_name, None, str(e)

        tasks = [_search_db(db) for db in dbs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                logger.warning("Database search task failed: %s", r)
                continue
            db_name, db_result, error = r
            if error:
                result.errors[db_name] = error
                continue
            if db_result:
                result.databases_used.append(db_name)
                result.results_by_db[db_name] = db_result

        result.merged_items = self._merge_results(result.results_by_db)
        result.total_count = len(result.merged_items)
        result.merged_items = result.merged_items[:max_results]

        logger.info(
            "Aggregated search: query='%s', dbs=%s, total=%d, errors=%d",
            query[:30], result.databases_used, result.total_count, len(result.errors),
        )
        return result

    async def fetch(
        self,
        identifier: str,
        database: str,
    ) -> DatabaseResult | None:
        connector = await self._get_connector(database)
        if connector is None:
            logger.warning("Unknown database for fetch: %s", database)
            return None
        try:
            return await connector.fetch(identifier)
        except Exception as e:
            logger.warning("Fetch failed for %s/%s: %s", database, identifier, e)
            return None

    async def close_all(self) -> None:
        for name, connector in self._connectors.items():
            try:
                await connector.close()
            except Exception as e:
                logger.warning("Error closing connector %s: %s", name, e)
        self._connectors.clear()

    async def _get_connector(self, db_name: str) -> BaseConnector | None:
        if db_name in self._connectors:
            return self._connectors[db_name]

        entry = _CONNECTOR_MAP.get(db_name.lower())
        if not entry:
            logger.warning("No connector mapping for: %s", db_name)
            return None

        module_path, class_name = entry
        try:
            import importlib
            module = importlib.import_module(module_path)
            connector_cls = getattr(module, class_name)

            kwargs = {}
            if db_name.lower() == "pubmed":
                kwargs["email"] = "research@localhost"

            connector = connector_cls(**kwargs)
            self._connectors[db_name] = connector
            logger.debug("Created connector: %s", db_name)
            return connector
        except Exception as e:
            logger.error("Failed to create connector %s: %s", db_name, e)
            return None

    def _merge_results(
        self,
        results_by_db: dict[str, DatabaseResult],
    ) -> list[dict]:
        all_items: list[dict] = []
        seen: set[str] = set()

        for db_name, db_result in results_by_db.items():
            for item in db_result.items:
                dedup_key = self._item_dedup_key(item, db_name)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                item["_source_db"] = db_name
                all_items.append(item)

        all_items.sort(key=lambda x: x.get("_relevance", 0.0), reverse=True)
        return all_items

    def _item_dedup_key(self, item: dict, db_name: str) -> str:
        for key in ["doi", "pmid", "pdb_id", "uniprot_id", "ensembl_id", "chembl_id"]:
            val = item.get(key, "")
            if val:
                return f"{key}:{val}"
        title = item.get("title", item.get("name", ""))
        if title:
            return f"title:{title.lower().strip()[:80]}"
        return f"db:{db_name}:id:{id(item)}"
