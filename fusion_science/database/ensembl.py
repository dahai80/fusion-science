"""Ensembl connector — retrieve genomic data, genes, variants, and annotations.

Uses the Ensembl REST API (https://rest.ensembl.org/) with support for
domestic mirrors (Ensembl Asia/Gencode China mirrors).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class EnsemblConnector(BaseConnector):
    """Connector for Ensembl genomic database.

    Uses Ensembl REST API (rest.ensembl.org).
    Domestic mirror: Ensembl Asia mirror (use_mirror=True).
    """

    BASE_URL = "https://rest.ensembl.org"
    MIRROR_URL = "https://rest.ensembl.org"  # Ensembl 亚洲镜像: useast.ensembl.org

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")
        mirror_url = os.getenv("FUSION_SCI_ENSEMBL_MIRROR", self.MIRROR_URL)
        config = ConnectorConfig(
            base_url=self.BASE_URL,
            mirror_url=mirror_url,
            use_mirror=use_mirror,
            offline_mode=offline_mode,
            timeout=30.0,
            rate_limit=0.2,
        )
        super().__init__(config)

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        """Make a GET request and return JSON with Ensembl headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self._request_with_retry(
            "GET",
            path,
            params=params,
            headers=headers,
        )
        return resp.json()

    async def search(self, query: str, max_results: int = 20, **kwargs) -> DatabaseResult:
        """Search Ensembl for genes, transcripts, or features.

        Args:
            query: Search query (e.g., "BRCA1", "TP53 human").
            max_results: Maximum results.
            **kwargs: Additional search parameters (e.g., species).

        Returns:
            DatabaseResult with matching genomic entries.
        """
        cache_key = f"search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            species = kwargs.get("species", "human")
            data = await self._get_json(
                "/search",
                params={
                    "q": query,
                    "species": species,
                    "limit": str(max_results),
                },
            )

            # The search endpoint returns a list
            results = data if isinstance(data, list) else data.get("results", [])
            items = []
            for r in results:
                items.append(
                    {
                        "id": r.get("id", ""),
                        "type": r.get("type", ""),
                        "description": r.get("description", ""),
                        "species": r.get("species", ""),
                        "region": r.get("region", ""),
                        "start": r.get("start", 0),
                        "end": r.get("end", 0),
                        "strand": r.get("strand", 0),
                        "source": "Ensembl",
                    }
                )

            result = DatabaseResult(
                source="ensembl",
                query=query,
                items=items,
                total_count=len(items),
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("Ensembl search failed: %s", e)
            return DatabaseResult(
                source="ensembl",
                query=query,
                error=str(e),
            )

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch genomic data by Ensembl ID.

        Args:
            identifier: Ensembl gene/transcript/protein ID (e.g., "ENSG00000141510" for TP53).
            **kwargs: Additional parameters.

        Returns:
            DatabaseResult with the genomic entry.
        """
        cache_key = f"fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            data = await self._get_json(f"/lookup/id/{identifier}", params={"expand": "1"})
            entry = {
                "id": data.get("id", ""),
                "type": data.get("object_type", ""),
                "description": data.get("description", ""),
                "species": data.get("species", ""),
                "assembly": data.get("assembly_name", ""),
                "region": data.get("seq_region_name", ""),
                "start": data.get("start", 0),
                "end": data.get("end", 0),
                "strand": data.get("strand", 0),
                "biotype": data.get("biotype", ""),
                "display_name": data.get("display_name", ""),
                "version": data.get("version", 0),
                "source": "Ensembl",
            }

            result = DatabaseResult(
                source="ensembl",
                query=identifier,
                items=[entry],
                total_count=1,
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("Ensembl fetch failed for %s: %s", identifier, e)
            return DatabaseResult(
                source="ensembl",
                query=identifier,
                error=str(e),
            )

    async def fetch_gene(self, gene_id: str, species: str = "human") -> dict[str, Any]:
        """Fetch detailed information about a gene.

        Args:
            gene_id: Ensembl gene ID (e.g., "ENSG00000141510").
            species: Species name (e.g., "human", "mouse").

        Returns:
            Dict with gene details including transcripts and homologues.
        """
        try:
            data = await self._get_json(
                f"/lookup/id/{gene_id}",
                params={
                    "expand": "1",
                    "format": "json",
                },
            )
            return {
                "gene_id": data.get("id", ""),
                "display_name": data.get("display_name", ""),
                "description": data.get("description", ""),
                "biotype": data.get("biotype", ""),
                "chromosome": data.get("seq_region_name", ""),
                "start": data.get("start", 0),
                "end": data.get("end", 0),
                "strand": data.get("strand", 0),
                "version": data.get("version", 0),
                "source": "Ensembl",
            }
        except Exception as e:
            logger.error("Failed to fetch gene %s: %s", gene_id, e)
            return {"error": str(e), "gene_id": gene_id}

    async def fetch_sequence_region(self, species: str, region: str, start: int, end: int) -> str:
        """Fetch genomic sequence for a specific region.

        Args:
            species: Species name (e.g., "human").
            region: Chromosome/region name (e.g., "17").
            start: Start position.
            end: End position.

        Returns:
            DNA sequence string.
        """
        try:
            resp = await self._request_with_retry(
                "GET",
                f"/sequence/region/{species}/{region}:{start}..{end}",
                headers={"Content-Type": "text/plain", "Accept": "text/plain"},
            )
            return resp.text.strip()
        except Exception as e:
            # P0 (E6): do NOT return "" on failure — an empty sequence is
            # indistinguishable from "no sequence found" and a scientist could
            # publish a negative finding that was really a network outage.
            # Raise so callers distinguish upstream failure from empty data.
            logger.error("Failed to fetch sequence region: %s", e)
            raise RuntimeError(f"ensembl_fetch_sequence_region_failed: {e}") from e

    async def fetch_variants(self, gene_id: str, species: str = "human") -> list[dict[str, Any]]:
        """Fetch variants for a gene.

        Args:
            gene_id: Ensembl gene ID.
            species: Species name.

        Returns:
            List of variant dicts.
        """
        try:
            data = await self._get_json(
                f"/overlap/id/{gene_id}",
                params={"feature": "variation"},
            )
            results = data if isinstance(data, list) else []
            variants = []
            for r in results[:100]:  # Limit to 100 variants
                variants.append(
                    {
                        "id": r.get("id", ""),
                        "allele_string": r.get("allele_string", ""),
                        "start": r.get("start", 0),
                        "end": r.get("end", 0),
                        "strand": r.get("strand", 0),
                        "consequence": r.get("consequence_type", ""),
                        "clinical_significance": r.get("clinical_significance", ""),
                    }
                )
            return variants
        except Exception as e:
            # P0 (E6): do NOT return [] on failure — empty variants look like
            # "no variants exist" vs an Ensembl outage. Raise to surface it.
            logger.error("Failed to fetch variants for %s: %s", gene_id, e)
            raise RuntimeError(f"ensembl_fetch_variants_failed: {e}") from e

    async def fetch_homologues(self, gene_id: str, species: str = "human") -> list[dict[str, Any]]:
        """Fetch homologous genes across species.

        Args:
            gene_id: Ensembl gene ID.
            species: Species name.

        Returns:
            List of homologue dicts.
        """
        try:
            data = await self._get_json(
                f"/homology/id/{gene_id}",
                params={
                    "format": "json",
                    "type": "orthologues",
                },
            )
            homologies = data.get("data", [])
            results = []
            for h in homologies:
                for hom in h.get("homologies", []):
                    target = hom.get("target", {})
                    results.append(
                        {
                            "gene_id": target.get("id", ""),
                            "species": target.get("species", {}).get("name", ""),
                            "type": hom.get("type", ""),
                            "identity": hom.get("identity", 0),
                            "cigar": hom.get("cigar_line", ""),
                        }
                    )
            return results
        except Exception as e:
            # P0 (E6): do NOT return [] on failure — see fetch_variants note.
            logger.error("Failed to fetch homologues for %s: %s", gene_id, e)
            raise RuntimeError(f"ensembl_fetch_homologues_failed: {e}") from e

    async def search_by_gene_name(self, gene_name: str, species: str = "human") -> DatabaseResult:
        """Search for a gene by its common name.

        Args:
            gene_name: Gene symbol (e.g., "TP53", "BRCA1").
            species: Species name.

        Returns:
            DatabaseResult with matching genes.
        """
        return await self.search(gene_name, max_results=10, species=species)
