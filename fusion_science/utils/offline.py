from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger(__name__)


def is_offline() -> bool:
    if os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes"):
        return True
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return False
    except OSError:
        logger.info("Network unreachable — auto-detected offline mode")
        return True


def get_connectivity() -> dict:
    offline = is_offline()
    result = {"offline": offline, "env_override": os.getenv("FUSION_OFFLINE_MODE", "")}
    if not offline:
        mirrors = {
            "pubmed": "https://eutils.ncbi.nlm.nih.gov",
            "pdb": "https://data.rcsb.org",
            "uniprot": "https://rest.uniprot.org",
        }
        for name, url in mirrors.items():
            try:
                socket.create_connection((url.split("//")[1].split("/")[0], 443), timeout=3)
                result[name] = "reachable"
            except OSError:
                result[name] = "unreachable"
    return result
