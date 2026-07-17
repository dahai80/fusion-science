"""Domestic mirror configuration and utility functions.

Provides helper functions for configuring domestic database mirrors,
offline cache, and other China-specific research environment utilities.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mirror configuration helpers
# ---------------------------------------------------------------------------

def get_mirror_config() -> dict[str, Any]:
    """Get the mirror configuration for the domestic research environment.

    Returns:
        Dict with mirror configuration.
    """
    return {
        "enabled": os.environ.get("FUSION_SCIENCE_USE_MIRRORS", "false").lower() == "true",
        "mirrors": {
            "pubmed": {
                "primary": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
                "mirror": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
                "note": "PubMed has no official domestic mirror; use CNKI for Chinese literature",
            },
            "uniprot": {
                "primary": "https://rest.uniprot.org",
                "mirror": "https://rest.uniprot.org",
                "note": "UniProt accessible via academic networks; local cache recommended",
            },
            "pdb": {
                "primary": "https://data.rcsb.org/rest/v1",
                "mirror": "https://data.rcsb.org/rest/v1",
                "note": "PDBe (Europe) often more accessible from China",
            },
            "ensembl": {
                "primary": "https://rest.ensembl.org",
                "mirror": "https://useast.ensembl.org",
                "note": "Ensembl US East mirror recommended for Asia",
            },
            "chembl": {
                "primary": "https://www.ebi.ac.uk/chembl/api/data",
                "mirror": "https://www.ebi.ac.uk/chembl/api/data",
                "note": "ChEMBL at EBI; accessible via academic networks",
            },
        },
        "chinese_databases": {
            "CNGB": "https://www.cngb.org/",
            "NGDC": "https://ngdc.cncb.ac.cn/",
            "CNKI": "https://www.cnki.net/",
            "CBM": "https://www.sinomed.ac.cn/",
            "ScienceDB": "https://www.scidb.cn/",
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
        {"name": "CNKI", "type": "literature", "connector": None, "offline": True, "note": "Chinese literature database"},
        {"name": "NGDC", "type": "genomics", "connector": None, "offline": True, "note": "National Genomics Data Center"},
    ]


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics for the offline cache.

    Returns:
        Cache statistics dict.
    """
    from .database.mirror import ScienceCache
    cache = ScienceCache()
    return cache.stats()


def clear_cache(source: str | None = None) -> int:
    """Clear the offline cache.

    Args:
        source: Optional source to clear (e.g., "pubmed"). If None, clears all.

    Returns:
        Number of entries cleared.
    """
    from .database.mirror import ScienceCache
    cache = ScienceCache()
    stats = cache.stats()
    total = stats.get("total_entries", 0)
    cache.clear(source=source)
    return total