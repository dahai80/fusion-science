"""Domestic mirror configuration and utility functions.

Provides helper functions for configuring domestic database mirrors,
offline cache, and other China-specific research environment utilities.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mirror configuration helpers
# ---------------------------------------------------------------------------


def get_mirror_config() -> dict[str, Any]:
    """Get the mirror configuration for the domestic research environment.

    Returns:
        Dict with mirror configuration, reading from environment variables.
    """
    return {
        "enabled": os.environ.get("FUSION_SCIENCE_USE_MIRRORS", "false").lower() == "true",
        "offline_mode": os.environ.get("FUSION_OFFLINE_MODE", "false").lower() in ("true", "1", "yes"),
        "mirrors": {
            "pubmed": {
                "primary": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
                "mirror": os.environ.get("FUSION_SCI_PUBMED_MIRROR", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"),
                "note": "PubMed无官方国内镜像；中文文献替代: CNKI (https://www.cnki.net)",
            },
            "uniprot": {
                "primary": "https://rest.uniprot.org",
                "mirror": os.environ.get("FUSION_SCI_UNIPROT_MIRROR", "https://rest.uniprot.org"),
                "note": "UniProt可通过学术网络访问；建议预缓存参考蛋白质组到本地",
            },
            "pdb": {
                "primary": "https://data.rcsb.org/rest/v1",
                "mirror": os.environ.get("FUSION_SCI_PDB_MIRROR", "https://data.rcsb.org/rest/v1"),
                "note": "PDBe (欧洲) 从中国访问比RCSB更稳定；建议下载年度发布包",
            },
            "ensembl": {
                "primary": "https://rest.ensembl.org",
                "mirror": os.environ.get("FUSION_SCI_ENSEMBL_MIRROR", "https://useast.ensembl.org"),
                "note": "Ensembl US East 镜像推荐用于亚洲区域",
            },
            "chembl": {
                "primary": "https://www.ebi.ac.uk/chembl/api/data",
                "mirror": os.environ.get("FUSION_SCI_CHEMBL_MIRROR", "https://www.ebi.ac.uk/chembl/api/data"),
                "note": "ChEMBL 位于EBI英国；需学术网络出口；建议离线缓存常用查询",
            },
        },
        "chinese_databases": {
            "NGDC": "https://ngdc.cncb.ac.cn",
            "CNGB": "https://www.cngb.org",
            "BIGD": "https://bigd.big.ac.cn",
            "GSA": "https://ngdc.cncb.ac.cn/gsa",
            "GWH": "https://ngdc.cncb.ac.cn/gwh",
            "CNKI": "https://www.cnki.net",
            "CBM": "https://www.sinomed.ac.cn",
            "ScienceDB": "https://www.scidb.cn",
            "PDB_CN": "https://pdb.cn",
        },
    }


def get_offline_recommendation() -> str:
    """Get recommendations for offline operation in the domestic environment.

    Returns:
        Markdown-formatted recommendation text.
    """
    return """## Offline Operation Recommendations

### Database Access
- **PubMed**: Use CNKI (https://www.cnki.net) for Chinese literature; pre-cache PubMed results when connected.
- **UniProt**: Download the UniProt reference proteomes for your species of interest for offline use.
- **PDB**: Use the PDB annual release bundle for offline structure analysis.
- **Ensembl**: Download the Ensembl gene annotation GTF/GFF files for your species.

### Local Cache
Enable the SQLite-based cache to reduce repeated network requests:
```python
from fusion_science.database.mirror import ScienceCache
cache = ScienceCache()
cache.set("my_key", my_data, source="pubmed")
```

### Network Configuration
For academic institutions in China, configure your network proxy:
```bash
export HTTP_PROXY=http://your-proxy:port
export HTTPS_PROXY=http://your-proxy:port
```

Or use the institution's VPN/SSH tunnel for international database access.
"""


def get_available_databases() -> list[dict[str, str]]:
    """List all available scientific databases with their status.

    Returns:
        List of database info dicts.
    """
    return [
        {"name": "PubMed", "type": "literature", "connector": "PubMedConnector", "offline": False},
        {"name": "UniProt", "type": "protein", "connector": "UniProtConnector", "offline": False},
        {"name": "PDB", "type": "structure", "connector": "PDBConnector", "offline": False},
        {"name": "Ensembl", "type": "genomics", "connector": "EnsemblConnector", "offline": False},
        {"name": "ChEMBL", "type": "drug", "connector": "ChEMBLConnector", "offline": False},
        {"name": "arXiv", "type": "preprint", "connector": "LiteratureSearch", "offline": False},
        {
            "name": "CNKI",
            "type": "literature",
            "connector": None,
            "offline": True,
            "note": "Chinese literature database",
        },
        {
            "name": "NGDC",
            "type": "genomics",
            "connector": None,
            "offline": True,
            "note": "National Genomics Data Center",
        },
    ]


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics for the offline cache.

    Returns:
        Cache statistics dict.
    """
    from fusion_science.database.mirror import get_shared_cache

    return get_shared_cache().stats()


def clear_cache(source: str | None = None) -> int:
    """Clear the offline cache.

    Args:
        source: Optional source to clear (e.g., "pubmed"). If None, clears all.

    Returns:
        Number of entries cleared.
    """
    from fusion_science.database.mirror import get_shared_cache

    cache = get_shared_cache()
    stats = cache.stats()
    total = stats.get("total_entries", 0)
    cache.clear(source=source)
    return total
