"""UniProt connector — retrieve protein sequence, function, and annotation data.

Uses the UniProt REST API (https://rest.uniprot.org/) with support for
domestic mirror fallback (CNCB/National Genomics Data Center).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class UniProtConnector(BaseConnector):
    """Connector for UniProt protein database.

    Uses UniProt REST API (uniprot.org).
    Domestic mirror: CNCB mirror (for Chinese research environments).
    """

    BASE_URL = "https://rest.uniprot.org"
    MIRROR_URL = "https://rest.uniprot.org"  # 无官方国内镜像；建议本地缓存参考蛋白质组

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")
        mirror_url = os.getenv("FUSION_SCI_UNIPROT_MIRROR", self.MIRROR_URL)
        config = ConnectorConfig(
            base_url=self.BASE_URL,
            mirror_url=mirror_url,
            use_mirror=use_mirror,
            offline_mode=offline_mode,
            timeout=30.0,
            rate_limit=0.1,
        )
        super().__init__(config)

    async def search(self, query: str, max_results: int = 20, **kwargs) -> DatabaseResult:
        """Search UniProt for proteins matching the query.

        Args:
            query: Search query (e.g., "human p53", "BRCA1 AND human").
            max_results: Maximum results to return.
            **kwargs: Additional search parameters.

        Returns:
            DatabaseResult with matched protein entries.
        """
        cache_key = f"search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        params = {
            "query": query,
            "size": str(max_results),
            "format": "json",
        }

        try:
            resp = await self._request_with_retry(
                "GET", "/uniprotkb/search", params=params
            )
            data = resp.json()

            results = data.get("results", [])
            items = [self._parse_entry(r) for r in results]
            total_count = data.get("total", len(results))

            result = DatabaseResult(
                source="uniprot",
                query=query,
                items=items,
                total_count=total_count,
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("UniProt search failed: %s", e)
            return DatabaseResult(
                source="uniprot",
                query=query,
                error=str(e),
            )

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch a UniProt entry by accession.

        Args:
            identifier: UniProt accession (e.g., "P04637" for human TP53).
            **kwargs: Additional parameters.

        Returns:
            DatabaseResult with the protein entry.
        """
        cache_key = f"fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            resp = await self._request_with_retry(
                "GET", f"/uniprotkb/{identifier}", params={"format": "json"}
            )
            data = resp.json()
            entry = self._parse_entry(data)

            result = DatabaseResult(
                source="uniprot",
                query=identifier,
                items=[entry] if entry else [],
                total_count=1 if entry else 0,
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("UniProt fetch failed: %s", e)
            return DatabaseResult(
                source="uniprot",
                query=identifier,
                error=str(e),
            )

    async def fetch_sequence(self, accession: str) -> str:
        """Fetch the raw protein sequence for a UniProt accession.

        Args:
            accession: UniProt accession (e.g., "P04637").

        Returns:
            Protein sequence as a string.
        """
        try:
            resp = await self._request_with_retry(
                "GET", f"/uniprotkb/{accession}.fasta"
            )
            lines = resp.text.split("\n")
            # Skip header line (starts with >)
            seq = "".join(line.strip() for line in lines if line and not line.startswith(">"))
            return seq
        except Exception as e:
            logger.error("Failed to fetch sequence for %s: %s", accession, e)
            return ""

    def _parse_entry(self, entry: dict) -> dict[str, Any]:
        """Parse a UniProt API entry into a structured dict.

        Args:
            entry: Raw JSON entry from UniProt API.

        Returns:
            Structured dict with key protein information.
        """
        # Primary accession
        accession = entry.get("primaryAccession", "")
        # Secondary accessions
        secondary = entry.get("secondaryAccessions", [])

        # Protein name
        protein_desc = entry.get("proteinDescription", {})
        recommended_name = protein_desc.get("recommendedName", {})
        full_name = ""
        if recommended_name:
            full_name = recommended_name.get("fullName", {}).get("value", "")
        # Short names
        short_names = []
        for sn in recommended_name.get("shortNames", []):
            short_names.append(sn.get("value", ""))

        # Gene names
        genes = entry.get("genes", [])
        gene_names = []
        for gene in genes:
            for gn in gene.get("geneName", []):
                gene_names.append(gn.get("value", ""))
            for syn in gene.get("synonyms", []):
                gene_names.append(syn.get("value", ""))

        # Organism
        organism = entry.get("organism", {})
        scientific_name = organism.get("scientificName", "")
        common_name = organism.get("commonName", "")
        taxon_id = organism.get("taxonId", 0)

        # Sequence
        sequence = entry.get("sequence", {})
        seq_length = sequence.get("length", 0)
        seq_mass = sequence.get("molWeight", 0)
        seq_value = sequence.get("value", "")

        # Function / comments
        comments = entry.get("comments", [])
        function = ""
        for comment in comments:
            if comment.get("commentType") == "FUNCTION":
                texts = comment.get("texts", [])
                if texts:
                    function = texts[0].get("value", "")

        # Features
        features = []
        for feat in entry.get("features", []):
            features.append({
                "type": feat.get("type", ""),
                "description": feat.get("description", ""),
                "location": {
                    "start": feat.get("location", {}).get("start", {}).get("value"),
                    "end": feat.get("location", {}).get("end", {}).get("value"),
                },
            })

        # Keywords
        keywords = [kw.get("name", "") for kw in entry.get("keywords", [])]

        # Cross-references
        db_refs = {}
        for ref in entry.get("uniProtKBCrossReferences", []):
            db = ref.get("database", "")
            db_refs[db] = [r.get("id") for r in [ref]]

        return {
            "accession": accession,
            "secondary_accessions": secondary,
            "protein_name": full_name,
            "short_names": short_names,
            "gene_names": gene_names,
            "organism": scientific_name,
            "common_name": common_name,
            "taxon_id": taxon_id,
            "sequence_length": seq_length,
            "molecular_weight": seq_mass,
            "sequence": seq_value,
            "function": function,
            "features": features,
            "keywords": keywords,
            "cross_references": db_refs,
            "source": "UniProt",
        }

    async def search_by_gene(self, gene_name: str, organism: str = "human") -> DatabaseResult:
        """Search UniProt by gene name.

        Args:
            gene_name: Gene symbol (e.g., "TP53", "BRCA1").
            organism: Organism name (e.g., "human", "mouse", "E. coli").

        Returns:
            DatabaseResult with matched protein entries.
        """
        return await self.search(f"({gene_name}) AND (organism:{organism})", max_results=10)

    async def search_by_taxon(self, taxon_id: int, max_results: int = 50) -> DatabaseResult:
        """Search UniProt entries by taxonomy ID.

        Args:
            taxon_id: NCBI taxonomy ID (e.g., 9606 for human).
            max_results: Maximum results.

        Returns:
            DatabaseResult with matched protein entries.
        """
        return await self.search(f"taxonomy_id:{taxon_id}", max_results=max_results)
