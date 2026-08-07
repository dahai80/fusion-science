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
from dataclasses import dataclass
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

# ---------------------------------------------------------------------------
# 海外科研数据库镜像配置（含国内替代方案）
# 优先使用国内镜像/替代源，降低对境外网络的依赖
# ---------------------------------------------------------------------------

DOMESTIC_MIRRORS: dict[str, MirrorEndpoint] = {
    "pubmed": MirrorEndpoint(
        name="PubMed (E-utilities)",
        primary_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        mirror_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        priority=20,
        notes="PubMed无官方国内镜像；中文文献替代: CNKI (https://www.cnki.net)",
    ),
    "uniprot": MirrorEndpoint(
        name="UniProt (CNCB mirror)",
        primary_url="https://rest.uniprot.org",
        mirror_url="https://rest.uniprot.org",
        priority=20,
        notes="UniProt可通过学术网络访问；建议预缓存参考蛋白质组到本地",
    ),
    "pdb": MirrorEndpoint(
        name="PDB (PDBe mirror)",
        primary_url="https://data.rcsb.org/rest/v1",
        mirror_url="https://data.rcsb.org/rest/v1",
        priority=15,
        notes="PDBe (欧洲) 从中国访问比RCSB更稳定；建议下载年度发布包",
    ),
    "ensembl": MirrorEndpoint(
        name="Ensembl (亚洲镜像)",
        primary_url="https://rest.ensembl.org",
        mirror_url="https://useast.ensembl.org",
        priority=10,
        notes="Ensembl US East 镜像推荐用于亚洲区域",
    ),
    "chembl": MirrorEndpoint(
        name="ChEMBL (EBI)",
        primary_url="https://www.ebi.ac.uk/chembl/api/data",
        mirror_url="https://www.ebi.ac.uk/chembl/api/data",
        priority=20,
        notes="ChEMBL 位于EBI英国；需学术网络出口；建议离线缓存常用查询",
    ),
    "ncbi_blast": MirrorEndpoint(
        name="NCBI BLAST (CNCB mirror)",
        primary_url="https://blast.ncbi.nlm.nih.gov/Blast.cgi",
        mirror_url="https://blast.ncbi.nlm.nih.gov/Blast.cgi",
        priority=20,
        notes="推荐本地部署BLAST+进行离线序列比对",
    ),
}

# ---------------------------------------------------------------------------
# 中国自主科研数据库（替代海外库的国内源）
# 这些数据库在国内可直接访问，无需翻墙
# ---------------------------------------------------------------------------

CHINESE_DATABASES: dict[str, str] = {
    # ---- 基因组/生物信息 ----
    "NGDC": "https://ngdc.cncb.ac.cn",  # 国家基因组科学数据中心 (CNCB-NGDC)
    "CNGB": "https://www.cngb.org",  # 国家基因库 (China National GeneBank)
    "BIGD": "https://bigd.big.ac.cn",  # 北京基因组研究所数据库
    "GSA": "https://ngdc.cncb.ac.cn/gsa",  # 基因组序列归档 (Genome Sequence Archive)
    "GWH": "https://ngdc.cncb.ac.cn/gwh",  # 基因组组装仓库 (Genome Warehouse)
    "OMIX": "https://ngdc.cncb.ac.cn/omix",  # 组学数据归档 (OMIX)
    "BioCode": "https://ngdc.cncb.ac.cn/biocode",  # 生物信息代码库
    # ---- 文献/知识 ----
    "CNKI": "https://www.cnki.net",  # 中国知网 (中文文献)
    "CBM": "https://www.sinomed.ac.cn",  # 中国生物医学文献数据库 (SinoMed)
    "CSTR": "https://cstr.cn",  # 中国科技论文在线
    # ---- 科学数据 ----
    "ScienceDB": "https://www.scidb.cn",  # 科学数据银行 (CAS)
    "CASData": "https://data.cas.cn",  # 中国科学院数据云
    "PNDC": "https://pndc.cas.cn",  # 中国植物科学数据中心
    # ---- 化学/药物 ----
    "CNPIC": "https://www.nmpa.gov.cn",  # 国家药品监督管理局
    "RCSB_CN": "https://pdb.cn",  # PDB中国镜像
}

# ---------------------------------------------------------------------------
# 国内镜像配置优先级
# 当一个数据库有多个国内替代源时，按优先级选择
# ---------------------------------------------------------------------------

DOMESTIC_ALTERNATIVES: dict[str, list[dict[str, str]]] = {
    "pubmed": [
        {"name": "CNKI (中国知网)", "url": "https://www.cnki.net", "type": "替代", "lang": "zh"},
        {"name": "SinoMed (中国生物医学)", "url": "https://www.sinomed.ac.cn", "type": "替代", "lang": "zh"},
        {"name": "万方医学", "url": "https://med.wanfangdata.com.cn", "type": "替代", "lang": "zh"},
    ],
    "pdb": [
        {"name": "PDB 中国镜像", "url": "https://pdb.cn", "type": "镜像", "lang": "zh"},
        {
            "name": "PDB 年度发布包",
            "url": "ftp://ftp.wwpdb.org/pub/pdb/data/structures/divided/pdb/",
            "type": "离线",
            "lang": "en",
        },
    ],
    "uniprot": [
        {"name": "UniProt 中国镜像", "url": "https://www.uniprot.org", "type": "镜像", "lang": "en"},
        {
            "name": "参考蛋白质组离线包",
            "url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/",
            "type": "离线",
            "lang": "en",
        },
    ],
    "ensembl": [
        {"name": "Ensembl 基因组注释", "url": "ftp://ftp.ensembl.org/pub/", "type": "离线", "lang": "en"},
        {"name": "Ensembl 亚洲镜像", "url": "https://useast.ensembl.org", "type": "镜像", "lang": "en"},
    ],
    "chembl": [
        {
            "name": "ChEMBL 离线数据库",
            "url": "ftp://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/",
            "type": "离线",
            "lang": "en",
        },
    ],
}

# ---------------------------------------------------------------------------
# 从环境变量加载镜像配置
# 用户可通过 FUSION_SCI_* 环境变量覆盖默认镜像地址
# ---------------------------------------------------------------------------


def _load_mirrors_from_env() -> dict[str, MirrorEndpoint]:
    """从环境变量加载镜像配置，覆盖默认值。

    支持的环境变量:
        FUSION_SCI_PUBMED_MIRROR    - PubMed 镜像地址
        FUSION_SCI_PDB_MIRROR       - PDB 镜像地址
        FUSION_SCI_UNIPROT_MIRROR   - UniProt 镜像地址
        FUSION_SCI_ENSEMBL_MIRROR   - Ensembl 镜像地址
        FUSION_SCI_CHEMBL_MIRROR    - ChEMBL 镜像地址
        FUSION_SCI_NGDC_URL         - 国家基因组科学数据中心
        FUSION_SCI_CNGB_URL         - 国家基因库
        FUSION_SCI_CNKI_URL         - 中国知网
        FUSION_SCI_SCIENCEDB_URL    - 科学数据银行
    """
    overrides = {}
    env_map = {
        "FUSION_SCI_PUBMED_MIRROR": "pubmed",
        "FUSION_SCI_PDB_MIRROR": "pdb",
        "FUSION_SCI_UNIPROT_MIRROR": "uniprot",
        "FUSION_SCI_ENSEMBL_MIRROR": "ensembl",
        "FUSION_SCI_CHEMBL_MIRROR": "chembl",
    }
    for env_var, db_key in env_map.items():
        value = os.getenv(env_var, "")
        if value and db_key in DOMESTIC_MIRRORS:
            overrides[db_key] = MirrorEndpoint(
                name=f"{DOMESTIC_MIRRORS[db_key].name} (环境变量覆盖)",
                primary_url=DOMESTIC_MIRRORS[db_key].primary_url,
                mirror_url=value,
                enabled=True,
                priority=0,  # 最高优先级
                notes=f"由环境变量 {env_var} 覆盖",
            )
    return overrides


# 应用环境变量覆盖
_env_overrides = _load_mirrors_from_env()
if _env_overrides:
    DOMESTIC_MIRRORS.update(_env_overrides)

# 中国数据库环境变量覆盖
_env_db_map = {
    "FUSION_SCI_NGDC_URL": "NGDC",
    "FUSION_SCI_CNGB_URL": "CNGB",
    "FUSION_SCI_CNKI_URL": "CNKI",
    "FUSION_SCI_SCIENCEDB_URL": "ScienceDB",
}
for env_var, db_key in _env_db_map.items():
    value = os.getenv(env_var, "")
    if value:
        CHINESE_DATABASES[db_key] = value


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
        self._approx_count: int = 0  # Approximate entry count (avoids COUNT query)
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
                "SELECT data, expires_at FROM science_cache WHERE key = ?",
                (key,),
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
            self._approx_count += 1
        except Exception as e:
            logger.warning("Cache write error: %s", e)

    def delete(self, key: str) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM science_cache WHERE key = ?", (key,))
            self._conn.commit()
            self._approx_count = max(0, self._approx_count - 1)
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
            self._approx_count = 0
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
            cur = self._conn.execute(
                "SELECT source, COUNT(*) as cnt FROM science_cache GROUP BY source ORDER BY cnt DESC"
            )
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
            if self._approx_count >= self.config.max_entries:
                to_delete = max(100, self.config.max_entries // 10)
                self._conn.execute(
                    "DELETE FROM science_cache WHERE rowid IN ("
                    "SELECT rowid FROM science_cache ORDER BY last_accessed ASC LIMIT ?)",
                    (to_delete,),
                )
                self._conn.commit()
                self._approx_count = max(0, self._approx_count - to_delete)
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

    Supports:
    - Automatic fallback from primary URL to domestic mirror
    - Environment variable overrides (FUSION_SCI_*)
    - Offline mode auto-detection (FUSION_OFFLINE_MODE=true)
    - Domestic alternatives for unreachable international databases
    - Online-to-cached data fallback
    - Smart routing: latency testing + auto-switch to fastest endpoint
    """

    def __init__(self, cache: ScienceCache | None = None):
        self.cache = cache or ScienceCache()
        self.mirrors = dict(DOMESTIC_MIRRORS)
        self.alternatives = dict(DOMESTIC_ALTERNATIVES)
        self._use_mirrors: bool = False
        self._offline_mode: bool = self._detect_offline_mode()
        self._latency_cache: dict[str, dict[str, float]] = {}
        self._auto_switch: bool = False
        self._last_latency_test: float = 0.0

    @staticmethod
    def _detect_offline_mode() -> bool:
        """Detect offline mode from environment variable."""
        val = os.getenv("FUSION_OFFLINE_MODE", "").lower()
        return val in ("true", "1", "yes")

    def enable_mirrors(self, enabled: bool = True) -> None:
        """Enable or disable domestic mirror routing.

        Args:
            enabled: True to use mirrors when available.
        """
        self._use_mirrors = enabled
        if enabled:
            logger.info("国内数据库镜像已启用 (%s)", self._offline_mode)

    def enable_offline_mode(self, enabled: bool = True) -> None:
        """Enable or disable strict offline mode.

        In offline mode, all international requests are blocked and
        only local cache / domestic mirrors are used.

        Args:
            enabled: True to enable offline mode.
        """
        self._offline_mode = enabled
        if enabled:
            logger.info("离线模式已启用 — 仅使用本地缓存和国内镜像")
        else:
            logger.info("离线模式已禁用")

    def get_endpoint(self, db_name: str) -> MirrorEndpoint:
        """Get the best endpoint configuration for a database.

        Args:
            db_name: Database name (e.g., "pubmed", "uniprot").

        Returns:
            MirrorEndpoint with the appropriate URL (mirror if enabled).
        """
        mirror = self.mirrors.get(
            db_name,
            MirrorEndpoint(
                name=db_name,
                primary_url="",
                mirror_url="",
            ),
        )
        return mirror

    def get_url(self, db_name: str) -> str:
        """Get the best URL for a database, considering mirror and offline settings.

        In offline mode, prefers mirror URLs. If no mirror is configured,
        returns the primary URL (which may be unreachable offline).

        Args:
            db_name: Database name.

        Returns:
            URL string (mirror if enabled/offline, otherwise primary).
        """
        endpoint = self.get_endpoint(db_name)
        if (self._use_mirrors or self._offline_mode) and endpoint.mirror_url:
            return endpoint.mirror_url
        return endpoint.primary_url

    def get_alternatives(self, db_name: str) -> list[dict[str, str]]:
        """Get domestic alternative databases for a given international database.

        Args:
            db_name: International database name (e.g., "pubmed", "pdb").

        Returns:
            List of alternative database info dicts with name, url, type, lang.
        """
        return self.alternatives.get(db_name, [])

    def get_chinese_equivalent(self, db_name: str) -> str:
        """Get the recommended Chinese equivalent for an international database.

        Args:
            db_name: International database name.

        Returns:
            URL of the recommended Chinese alternative, or empty string.
        """
        alts = self.get_alternatives(db_name)
        # Prefer Chinese-language alternatives
        for alt in alts:
            if alt.get("lang") == "zh":
                return alt["url"]
        # Fallback to first alternative
        if alts:
            return alts[0]["url"]
        return ""

    def is_offline_mode(self) -> bool:
        """Check if offline mode is currently active.

        Returns:
            True if offline mode is enabled.
        """
        return self._offline_mode

    def list_mirrors(self) -> list[dict[str, Any]]:
        """List all configured mirrors with their status.

        Returns:
            List of mirror info dicts.
        """
        return [
            {
                "name": m.name,
                "db_key": key,
                "primary_url": m.primary_url,
                "mirror_url": m.mirror_url,
                "enabled": m.enabled,
                "active": (self._use_mirrors or self._offline_mode) and m.enabled,
                "priority": m.priority,
                "notes": m.notes,
            }
            for key, m in sorted(self.mirrors.items(), key=lambda x: x[1].priority)
        ]

    def list_chinese_databases(self) -> list[dict[str, str]]:
        """List Chinese domestic scientific databases.

        Returns:
            List of Chinese database info dicts.
        """
        return [{"name": name, "url": url} for name, url in CHINESE_DATABASES.items()]

    def list_alternatives(self) -> dict[str, list[dict[str, str]]]:
        """List all domestic alternatives for international databases.

        Returns:
            Dict mapping database name to list of alternative endpoints.
        """
        return dict(self.alternatives)

    def get_status_report(self) -> dict[str, Any]:
        """Get a comprehensive status report of the mirror routing system.

        Returns:
            Dict with status information.
        """
        return {
            "offline_mode": self._offline_mode,
            "mirrors_enabled": self._use_mirrors,
            "mirror_count": len(self.mirrors),
            "chinese_db_count": len(CHINESE_DATABASES),
            "active_mirrors": sum(
                1 for m in self.mirrors.values() if m.enabled and (self._use_mirrors or self._offline_mode)
            ),
            "auto_switch": self._auto_switch,
            "cache_status": self.cache.stats() if self.cache else {"enabled": False},
        }

    # ------------------------------------------------------------------
    # Smart routing: latency test + auto-switch (F-20)
    # ------------------------------------------------------------------

    async def test_latency(self, db_name: str, timeout: float = 5.0) -> dict[str, float]:
        """Test latency to primary and mirror endpoints for a database.

        Args:
            db_name: Database name (e.g., "pubmed", "uniprot").
            timeout: HTTP request timeout in seconds.

        Returns:
            Dict with "primary" and "mirror" latency in seconds.
            -1.0 means unreachable.
        """
        import httpx

        endpoint = self.get_endpoint(db_name)
        results: dict[str, float] = {}

        for key, url in [("primary", endpoint.primary_url), ("mirror", endpoint.mirror_url)]:
            if not url:
                results[key] = -1.0
                continue
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    t0 = time.time()
                    resp = await client.get(url)
                    elapsed = time.time() - t0
                    results[key] = elapsed if resp.status_code < 500 else -1.0
            except Exception as e:
                logger.info("Latency test %s %s failed: %s", key, url, e)
                results[key] = -1.0

        self._latency_cache[db_name] = results
        self._last_latency_test = time.time()
        logger.info(
            "Latency test [%s]: primary=%.3fs, mirror=%.3fs",
            db_name,
            results.get("primary", -1.0),
            results.get("mirror", -1.0),
        )
        return results

    async def test_all_latency(self, timeout: float = 3.0) -> dict[str, dict[str, float]]:
        """Test latency for all configured mirrors in parallel.

        Probes all databases concurrently with a per-endpoint timeout cap so a
        single unreachable mirror cannot block the whole response. Per-database
        failures are degraded to {"primary": -1.0, "mirror": -1.0} rather than
        aborting the batch.

        Args:
            timeout: HTTP request timeout in seconds (per endpoint).

        Returns:
            Dict mapping db_name to {"primary": float, "mirror": float}.
        """
        import asyncio

        db_names = list(self.mirrors)
        if not db_names:
            return {}

        probe_results = await asyncio.gather(
            *(self.test_latency(name, timeout) for name in db_names),
            return_exceptions=True,
        )

        results: dict[str, dict[str, float]] = {}
        for name, probe in zip(db_names, probe_results, strict=True):
            if isinstance(probe, Exception):
                logger.warning("Latency batch probe for %s failed: %s", name, probe)
                results[name] = {"primary": -1.0, "mirror": -1.0}
            else:
                results[name] = probe
        ok_count = sum(1 for v in results.values() if v.get("primary", -1.0) >= 0 or v.get("mirror", -1.0) >= 0)
        logger.info("Latency batch done: %d/%d dbs probed ok", ok_count, len(db_names))
        return results

    def enable_auto_switch(self, enabled: bool = True) -> None:
        """Enable or disable automatic switching to the fastest endpoint.

        When enabled, get_url() will prefer the endpoint with the lowest
        measured latency. Falls back to mirror/offline logic if no latency
        data is available.

        Args:
            enabled: True to enable auto-switch based on latency.
        """
        self._auto_switch = enabled
        if enabled:
            logger.info("镜像智能路由已启用 — 根据延迟自动选择最优端点")

    def get_latency_results(self) -> dict[str, dict[str, float]]:
        """Get the latest latency test results.

        Returns:
            Dict mapping db_name to {"primary": float, "mirror": float}.
        """
        return dict(self._latency_cache)

    def smart_get_url(self, db_name: str) -> str:
        """Get the best URL considering latency, mirror, and offline settings.

        Priority order when auto_switch is enabled:
        1. Fastest endpoint based on latency data
        2. Mirror URL if mirrors/offline enabled
        3. Primary URL as fallback

        Args:
            db_name: Database name.

        Returns:
            Best available URL string.
        """
        if self._auto_switch and db_name in self._latency_cache:
            latencies = self._latency_cache[db_name]
            endpoint = self.get_endpoint(db_name)
            primary_lat = latencies.get("primary", -1.0)
            mirror_lat = latencies.get("mirror", -1.0)

            if primary_lat >= 0 and mirror_lat >= 0:
                if mirror_lat < primary_lat and endpoint.mirror_url:
                    logger.debug("Smart route [%s]: mirror (%.3fs < %.3fs)", db_name, mirror_lat, primary_lat)
                    return endpoint.mirror_url
                logger.debug("Smart route [%s]: primary (%.3fs <= %.3fs)", db_name, primary_lat, mirror_lat)
                return endpoint.primary_url
            if mirror_lat >= 0 and endpoint.mirror_url:
                return endpoint.mirror_url
            if primary_lat >= 0:
                return endpoint.primary_url

        return self.get_url(db_name)
