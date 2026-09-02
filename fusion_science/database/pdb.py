"""PDB connector — retrieve 3D protein/nucleic acid structures.

Uses the RCSB PDB REST API (https://data.rcsb.org/) and the
PDB search API (https://search.rcsb.org/).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from .base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class PDBConnector(BaseConnector):
    """Connector for Protein Data Bank (PDB) — 3D macromolecular structures.

    Uses RCSB PDB data API (data.rcsb.org) and search API (search.rcsb.org).
    """

    DATA_URL = "https://data.rcsb.org/rest/v1"
    SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
    MIRROR_URL = "https://data.rcsb.org/rest/v1"  # PDBe (欧洲) 从中国访问更稳定

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")
        mirror_url = os.getenv("FUSION_SCI_PDB_MIRROR", self.MIRROR_URL)
        config = ConnectorConfig(
            base_url=self.DATA_URL,
            mirror_url=mirror_url,
            use_mirror=use_mirror,
            offline_mode=offline_mode,
            timeout=30.0,
            rate_limit=0.2,
        )
        self._search_client: httpx.AsyncClient | None = None
        super().__init__(config)

    @property
    def search_client(self) -> httpx.AsyncClient:
        # R-10: honor offline mode for the secondary search client too. Without
        # this, a PDB search would bypass the base client's offline guard and
        # still hit search.rcsb.org when FUSION_OFFLINE_MODE=true.
        if self.config.offline_mode:
            raise RuntimeError(
                f"离线模式已启用: {self.__class__.__name__} 无法发起网络请求。"
                "请设置 FUSION_OFFLINE_MODE=false 或直接传入离线数据。"
            )
        if self._search_client is None:
            self._search_client = httpx.AsyncClient(
                base_url=self.SEARCH_URL,
                timeout=self.config.timeout,
            )
        return self._search_client

    async def close(self) -> None:
        await super().close()
        if self._search_client:
            await self._search_client.aclose()
            self._search_client = None

    async def search(self, query: str, max_results: int = 20, **kwargs) -> DatabaseResult:
        """Search the PDB for structures matching the query.

        Args:
            query: Search query (e.g., "CRISPR Cas9", "SARS-CoV-2 spike").
            max_results: Maximum results to return.
            **kwargs: Additional search parameters.

        Returns:
            DatabaseResult with matched PDB entries.
        """
        cache_key = f"search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        search_payload = {
            "query": {
                "type": "terminal",
                "service": "text",
                "parameters": {
                    "value": query,
                },
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {
                    "start": 0,
                    "rows": max_results,
                },
                "scoring_strategy": "combined",
            },
        }

        try:
            resp = await self._request_with_retry(
                "POST",
                "",
                json=search_payload,
                client_override=self.search_client,
            )
            data = resp.json()
            pdb_ids = [r.get("identifier", "") for r in data.get("result_set", []) if r.get("identifier")]

            # I-13: fetch all matched entries concurrently instead of serially.
            # The prior loop issued one self.fetch per ID sequentially, each
            # paying the rate-limit delay — 20 results took 20*0.2s ~= 4s of
            # pure wait. gather collapses that to one round-trip window.
            detail_results = await asyncio.gather(
                *(self.fetch(pid) for pid in pdb_ids),
                return_exceptions=True,
            )
            items = []
            for detail in detail_results:
                if isinstance(detail, Exception):
                    logger.warning("PDB detail fetch failed: %s", detail)
                    continue
                if detail.items:
                    items.append(detail.items[0])

            result = DatabaseResult(
                source="pdb",
                query=query,
                items=items,
                total_count=len(pdb_ids),
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("PDB search failed: %s", e)
            return DatabaseResult(
                source="pdb",
                query=query,
                error=str(e),
            )

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch a PDB entry by its 4-character ID.

        Args:
            identifier: PDB ID (e.g., "6M0J" for SARS-CoV-2 spike protein).
            **kwargs: Additional parameters.

        Returns:
            DatabaseResult with the PDB entry details.
        """
        pdb_id = identifier.upper()
        cache_key = f"fetch:{pdb_id}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            # Fetch core entry data
            resp = await self._request_with_retry("GET", f"/core/entry/{pdb_id}")
            data = resp.json()

            # Fetch assembly data (for molecular weight, etc.)
            assembly_resp = await self._request_with_retry("GET", f"/core/assembly/{pdb_id}/1")
            assembly_data = assembly_resp.json()

            entry = self._parse_entry(data, assembly_data)

            result = DatabaseResult(
                source="pdb",
                query=pdb_id,
                items=[entry],
                total_count=1,
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("PDB fetch failed for %s: %s", pdb_id, e)
            return DatabaseResult(
                source="pdb",
                query=pdb_id,
                error=str(e),
            )

    def _parse_entry(self, data: dict, assembly_data: dict) -> dict[str, Any]:
        """Parse a PDB entry JSON into a structured dict.

        Args:
            data: Entry data from /core/entry/{id}.
            assembly_data: Assembly data from /core/assembly/{id}/1.

        Returns:
            Structured dict with PDB entry information.
        """
        struct = data.get("struct", {})
        rcsb_entry = data.get("rcsb_entry_info", {})
        exptl = data.get("exptl", [{}])
        audit_author = data.get("rcsb_audit_author", [])
        entity_count = len(data.get("rcsb_entry_container_identifiers", {}).get("entity_ids", []))

        # Title and description
        title = struct.get("title", "")
        description = rcsb_entry.get("structure_description", "")

        # Experimental method
        methods = []
        for exp in exptl:
            method = exp.get("method", "")
            if method:
                methods.append(method)

        # Resolution
        resolution = rcsb_entry.get("resolution_combined", [])
        resolution_value = resolution[0] if resolution else None

        # Deposited atoms
        atom_count = rcsb_entry.get("deposited_atom_count", 0)
        residue_count = rcsb_entry.get("deposited_model_count", 0)
        molecular_weight = assembly_data.get("rcsb_assembly_info", {}).get("molecular_weight", 0)

        # Authors
        authors = [a.get("name", "") for a in audit_author]

        # Polymer entities
        polymers = []
        try:
            polymer_entities = data.get("polymer_entities", [])
            for ent in polymer_entities:
                polymers.append(
                    {
                        "entity_id": ent.get("entity_id", 0),
                        "type": ent.get("polymer_entity_type", ""),
                        "sequence": ent.get("entity_poly", {}).get("pdbx_seq_one_letter_code_can", ""),
                        "length": ent.get("entity_poly", {}).get("pdbx_seq_one_letter_code_can", "").count("") - 1
                        if ent.get("entity_poly", {}).get("pdbx_seq_one_letter_code_can")
                        else 0,
                    }
                )
        except Exception:
            pass

        # Ligands
        ligands = []
        try:
            nonpolymer_entities = data.get("nonpolymer_entities", [])
            for ent in nonpolymer_entities:
                ligands.append(
                    {
                        "entity_id": ent.get("entity_id", 0),
                        "name": ent.get("pdbx_entity_nonpoly", {}).get("name", ""),
                        "comp_id": ent.get("pdbx_entity_nonpoly", {}).get("comp_id", ""),
                    }
                )
        except Exception:
            pass

        return {
            "pdb_id": data.get("rcsb_id", ""),
            "title": title,
            "description": description,
            "experimental_methods": methods,
            "resolution": resolution_value,
            "molecular_weight": molecular_weight,
            "atom_count": atom_count,
            "residue_count": residue_count,
            "entity_count": entity_count,
            "authors": authors,
            "polymers": polymers,
            "ligands": ligands,
            "deposition_date": data.get("rcsb_accession_info", {}).get("deposit_date", ""),
            "release_date": data.get("rcsb_accession_info", {}).get("initial_release_date", ""),
            "source": "PDB",
        }

    async def fetch_structure_url(self, pdb_id: str) -> dict[str, str]:
        """Get URLs for downloading PDB structure files.

        Args:
            pdb_id: PDB ID (e.g., "6M0J").

        Returns:
            Dict with URLs for different formats (pdb, mmcif, pdbml).
        """
        pdb_id = pdb_id.upper()
        return {
            "pdb": f"https://files.rcsb.org/download/{pdb_id}.pdb",
            "mmcif": f"https://files.rcsb.org/download/{pdb_id}.cif",
            "pdbml": f"https://files.rcsb.org/download/{pdb_id}.xml",
            "pdbml_ext": f"https://files.rcsb.org/download/{pdb_id}-ext.xml",
        }

    async def search_by_sequence(self, sequence: str, max_results: int = 10) -> DatabaseResult:
        """Search PDB by protein sequence.

        Args:
            sequence: Amino acid sequence string.
            max_results: Maximum results.

        Returns:
            DatabaseResult with structurally similar PDB entries.
        """
        search_payload = {
            "query": {
                "type": "terminal",
                "service": "sequence",
                "parameters": {
                    "evalue_cutoff": 0.1,
                    "identity_cutoff": 0.3,
                    "sequence_type": "protein",
                    "value": sequence,
                },
            },
            "return_type": "polymer_entity",
            "request_options": {
                "paginate": {"start": 0, "rows": max_results},
            },
        }

        try:
            resp = await self._request_with_retry(
                "POST",
                "",
                json=search_payload,
                client_override=self.search_client,
            )
            data = resp.json()
            result_set = data.get("result_set", [])
            # Extract unique PDB IDs, preserving first-seen order
            seen_ids: set[str] = set()
            pdb_ids: list[str] = []
            for r in result_set:
                ident = r.get("identifier", "")
                if not ident:
                    continue
                pid = ident.split("_")[0]
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    pdb_ids.append(pid)
            pdb_ids = pdb_ids[:max_results]

            # I-13: concurrent detail fetch (same N+1 as text search).
            detail_results = await asyncio.gather(
                *(self.fetch(pid) for pid in pdb_ids),
                return_exceptions=True,
            )
            items = []
            for detail in detail_results:
                if isinstance(detail, Exception):
                    logger.warning("PDB sequence-search detail fetch failed: %s", detail)
                    continue
                if detail.items:
                    items.append(detail.items[0])

            return DatabaseResult(
                source="pdb",
                query=f"sequence_similarity:{sequence[:20]}...",
                items=items,
                total_count=len(pdb_ids),
            )
        except Exception as e:
            logger.error("PDB sequence search failed: %s", e)
            return DatabaseResult(
                source="pdb",
                query="sequence_search",
                error=str(e),
            )
