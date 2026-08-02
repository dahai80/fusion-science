"""Scientific database connectors with domestic mirror support.

Provides unified access to life science databases (PubMed, UniProt, PDB,
Ensembl, ChEMBL, etc.) with automatic fallback to domestic mirrors
for the Chinese research environment.
"""

from __future__ import annotations

from .chinese import CNKIConnector, NGDCConnector, ScienceDBConnector
from .mirror import MirrorRouter

__all__ = ["NGDCConnector", "CNKIConnector", "ScienceDBConnector", "MirrorRouter"]
