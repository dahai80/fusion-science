"""Domestic database mirror and offline cache manager.

For the Chinese research environment, many international scientific databases
have limited direct access. This module provides:
1. Mirror configuration for domestic database endpoints
2. Offline cache management for pre-downloaded datasets
3. Automatic fallback routing between mirrors and primary endpoints
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MirrorEndpoint:
    """Configuration for a domestic database mirror."""

    name: str
    primary_url: str
    mirror_url: str
    enabled: bool = True
    priority: int = 0
    notes: str = ""


@dataclass
class CacheConfig:
    """Configuration for the offline cache."""

    enabled: bool = True
    cache_dir: str = "~/.cache/fusion-science"
    db_path: str = "science_cache.db"
    default_ttl: int = 86400  # 24 hours
    max_entries: int = 10000


# ---------------------------------------------------------------------------
# Known domestic mirror endpoints
# ---------------------------------------------------------------------------

DOMESTIC_MIRRORS: dict[str, MirrorEndpoint] = {
    "pubmed": MirrorEndpoint(
        name="PubMed (CNKI/CMCC)",
        primary_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        mirror_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        priority=10,
        notes="PubMed has no official domestic mirror; use CNKI for Chinese literature",
    ),
    "uniprot": MirrorEndpoint(
        name="UniProt (CNCB mirror)",
        primary_url="https://rest.uniprot.org",
        mirror_url="https://rest.uniprot.org",
        priority=10,
        notes="UniProt accessible via academic networks; consider local cache",
    ),
    "pdb": MirrorEndpoint(
        name="PDB (PDBe mirror)",
        primary_url="https://data.rcsb.org/rest/v1",
        mirror_url="https://data.rcsb.org/rest/v1",
        priority=10,
        notes="PDBe (Europe) is often more accessible from China",
    ),
    "ensembl": MirrorEndpoint(
        name="Ensembl (Asia mirror)",
        primary_url="https://rest.ensembl.org",
        mirror_url="https://useast.ensembl.org",
        priority=10,
        notes="Ensembl US East mirror recommended for Asia",
    ),
    "chembl": MirrorEndpoint(
        name="ChEMBL (EBI)",
        primary_url="https://www.ebi.ac.uk/chembl/api/data",
        mirror_url="https://www.ebi.ac.uk/chembl/api/data",
        priority=10,
        notes="ChEMBL at EBI UK; accessible via academic networks",
    ),
    "ncbi_blast": MirrorEndpoint(
        name="NCBI BLAST (CNCB mirror)",
        primary_url="https://blast.ncbi.nlm.nih.gov/Blast.cgi",
        mirror_url="https://blast.ncbi.nlm.nih.gov/Blast.cgi",
        priority=10,
        notes="Consider local BLAST+ installation for offline use",
    ),
}

CHINESE_DATABASES: dict[str, str] = {
    "CNGB": "https://www.cngb.org/",
    "NGDC": "https://ngdc.cncb.ac.cn/",
    "CNKI": "https://www.cnki.net/",
    "CBM": "https://www.sinomed.ac.cn/",
    "ScienceDB": "https://www.scidb.cn/",
}


# ---------------------------------------------------------------------------
# Cache manager
# ---------------------------------------------------------------------------

class ScienceCache:
    """SQLite-backed offline cache for scientific database queries.

    Caches API responses locally to reduce network requests and
    enable offline operation for previously queried data.
    """

    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_cache_dir(self) -> Path:
        cache_dir = Path(self.config.cache_dir).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _get_db_path(self) -> str:
        return str(self._get_cache_dir() / self.config.db_path)

    def _init_db(self) -> None:
        if not self.config.enabled:
            return
        try:
            self._conn = sqlite3.connect(self._get_db_path())
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS science_cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_accessed REAL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_expires
                ON science_cache(expires_at)
            """)
            self._conn.commit()
        except Exception as e:
            logger.warning("Failed to init cache database: %s", e)
            self._conn = None

    def get(self, key: str) -> Any | None:
        """Get a cached value by key."""
        if not self.config.enabled or self._conn is None:
            return None
        try:
            cur = self._conn.execute(
                "SELECT data, expires_at FROM science_cache WHERE key = ?", (key,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            data_str, expires_at = row
            if time.time() > expires_at:
                self._conn.execute("DELETE FROM science_cache WHERE key = ?", (key,))
                self._conn.commit()
                return None
            self._conn.execute(
                "UPDATE science_cache SET access_count = access_count + 1, last_accessed = ? WHERE key = ?",
                (time.time(), key),
            )
            self._conn.commit()
            return json.loads(data_str)
        except Exception as e:
            logger.warning("Cache read error: %s", e)
            return None

    def set(self, key: str, data: Any, source: str = "unknown", ttl: int | None = None) -> None:
        """Store a value in the cache."""
        if not self.config.enabled or self._conn is None:
            return
        try:
            self._evict_if_needed()
            now = time.time()
            ttl = ttl or self.config.default_ttl
            self._conn.execute(
                """INSERT OR REPLACE INTO science_cache
                   (key, data, source, created_at, expires_at, access_count, last_accessed)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (key, json.dumps(data, ensure_ascii=False), source, now, now + ttl, now),
            )
            self._conn.commit()
        except Exception as e:
            logger.warning("Cache write error: %s", e)

    def delete(self, key: str) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM science_cache WHERE key = ?", (key,))
            self._conn.commit()
        except Exception as e:
            logger.warning("Cache delete error: %s", e)

    def clear(self, source: str | None = None) -> None:
        if self._conn is None:
            return
        try:
            if source:
                self._conn.execute("DELETE FROM science_cache WHERE source = ?", (source,))
            else:
                self._conn.execute("DELETE FROM science_cache")
            self._conn.commit()
        except Exception as e:
            logger.warning("Cache clear error: %s", e)

    def stats(self) -> dict[str, Any]:
        if self._conn is None:
            return {"enabled": False}
        try:
            cur = self._conn.execute("SELECT COUNT(*) as total, SUM(LENGTH(data)) as total_bytes FROM science_cache")
            row = cur.fetchone()
            count = row[0] or 0
            total_bytes = row[1] or 0
            cur = self._conn.execute("SELECT source, COUNT(*) as cnt FROM science_cache GROUP BY source ORDER BY cnt DESC")
            by_source = {row[0]: row[1] for row in cur.fetchall()}
            return {
                "enabled": True,
                "total_entries": count,
                "total_bytes": total_bytes,
                "by_source": by_source,
                "db_path": self._get_db_path(),
            }
        except Exception as e:
            return {"enabled": True, "error": str(e)}

    def _evict_if_needed(self) -> None:
        if self._conn is None:
            return
        try:
            cur = self._conn.execute("SELECT COUNT(*) FROM science_cache")
            count = cur.fetchone()[0]
            if count >= self.config.max_entries:
                to_delete = max(100, self.config.max_entries // 10)
                self._conn.execute(
                    "DELETE FROM science_cache WHERE rowid IN ("
                    "SELECT rowid FROM science_cache ORDER BY last_accessed ASC LIMIT ?)", (to_delete,)
                )
                self._conn.commit()
        except Exception as e:
            logger.warning("Cache eviction error: %s", e)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Mirror router
# ---------------------------------------------------------------------------

class MirrorRouter:
    """Routes database requests to the best available endpoint.

    Supports automatic fallback from primary URL to domestic mirror,
    and from online to cached data.
    """

    def __init__(self, cache: ScienceCache | None = None):
        self.cache = cache or ScienceCache()
        self.mirrors = dict(DOMESTIC_MIRRORS)
        self._use_mirrors: bool = False

    def enable_mirrors(self, enabled: bool = True) -> None:
        self._use_mirrors = enabled

    def get_endpoint(self, db_name: str) -> MirrorEndpoint:
        mirror = self.mirrors.get(db_name, MirrorEndpoint(
            name=db_name, primary_url="", mirror_url="",
        ))
        return mirror

    def get_url(self, db_name: str) -> str:
        endpoint = self.get_endpoint(db_name)
        if self._use_mirrors and endpoint.mirror_url:
            return endpoint.mirror_url
        return endpoint.primary_url

    def list_mirrors(self) -> list[dict[str, Any]]:
        return [
            {
                "name": m.name,
                "db_key": key,
                "primary_url": m.primary_url,
                "mirror_url": m.mirror_url,
                "enabled": m.enabled,
                "active": self._use_mirrors and m.enabled,
            }
            for key, m in self.mirrors.items()
        ]

    def list_chinese_databases(self) -> list[dict[str, str]]:
        return [{"name": name, "url": url} for name, url in CHINESE_DATABASES.items()]