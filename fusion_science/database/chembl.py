"""ChEMBL connector — retrieve drug, compound, and bioactivity data.

Uses the ChEMBL REST API (https://www.ebi.ac.uk/chembl/api/data/)
for drug discovery and medicinal chemistry research.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


class ChEMBLConnector(BaseConnector):
    """Connector for ChEMBL — drug and bioactive molecule database.

    Uses ChEMBL REST API (ebi.ac.uk/chembl/api/data).
    """

    BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"
    MIRROR_URL = "https://www.ebi.ac.uk/chembl/api/data"  # ChEMBL 位于EBI，建议缓存常用查询

    def __init__(
        self,
        use_mirror: bool | None = None,
        offline_mode: bool | None = None,
    ):
        if use_mirror is None:
            use_mirror = os.getenv("FUSION_SCIENCE_USE_MIRRORS", "").lower() in ("true", "1", "yes")
        if offline_mode is None:
            offline_mode = os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")
        mirror_url = os.getenv("FUSION_SCI_CHEMBL_MIRROR", self.MIRROR_URL)
        config = ConnectorConfig(
            base_url=self.BASE_URL,
            mirror_url=mirror_url,
            use_mirror=use_mirror,
            offline_mode=offline_mode,
            timeout=30.0,
            rate_limit=0.3,
        )
        super().__init__(config)

    async def search(self, query: str, max_results: int = 20, **kwargs) -> DatabaseResult:
        """Search ChEMBL for molecules, targets, or assays.

        Args:
            query: Search query (e.g., "aspirin", "BRD4", "kinase inhibitor").
            max_results: Maximum results.
            **kwargs: Additional search parameters (entity_type: molecule, target, assay).

        Returns:
            DatabaseResult with search results.
        """
        cache_key = f"search:{query}:{max_results}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        # Default to molecule search
        entity = kwargs.get("entity_type", "molecule")
        search_type = kwargs.get("search_type", "similarity")

        try:
            if entity == "molecule":
                result = await self._search_molecules(query, max_results, search_type)
            elif entity == "target":
                result = await self._search_targets(query, max_results)
            else:
                result = await self._search_molecules(query, max_results, search_type)

            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("ChEMBL search failed: %s", e)
            return DatabaseResult(
                source="chembl",
                query=query,
                error=str(e),
            )

    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch a ChEMBL entry by its identifier.

        Args:
            identifier: ChEMBL ID (e.g., "CHEMBL25" for aspirin, "CHEMBL203" for imatinib).
            **kwargs: Additional parameters.

        Returns:
            DatabaseResult with the entry details.
        """
        cache_key = f"fetch:{identifier}"
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        try:
            # R-13: longest-prefix-first. "CHEMBL" is a prefix of "CHEMBL_TARGET"
            # and "CHEMBL_ASSAY"; checking the generic CHEMBL branch first would
            # misroute target/assay IDs (e.g. CHEMBL_TARGET1234) to _get_molecule,
            # hitting the wrong endpoint and returning a confusing 404.
            up = identifier.upper()
            if up.startswith("CHEMBL_TARGET"):
                data = await self._get_target(identifier)
            elif up.startswith("CHEMBL_ASSAY"):
                data = await self._get_assay(identifier)
            elif up.startswith("CHEMBL"):
                data = await self._get_molecule(identifier)
            else:
                # Try molecule first
                data = await self._get_molecule(identifier)

            result = DatabaseResult(
                source="chembl",
                query=identifier,
                items=[data] if data else [],
                total_count=1 if data else 0,
            )
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error("ChEMBL fetch failed for %s: %s", identifier, e)
            return DatabaseResult(
                source="chembl",
                query=identifier,
                error=str(e),
            )

    async def _search_molecules(self, query: str, max_results: int, search_type: str = "similarity") -> DatabaseResult:
        """Search for molecules in ChEMBL."""
        if search_type == "similarity":
            params = {
                "q": query,
                "limit": max_results,
                "format": "json",
            }
            resp = await self._request_with_retry("GET", "/molecule.json", params=params)
        else:
            params = {
                "q": query,
                "limit": max_results,
                "format": "json",
            }
            resp = await self._request_with_retry("GET", "/molecule.json", params=params)

        data = resp.json()
        molecules = data.get("molecules", data.get("results", []))
        items = [self._parse_molecule(m) for m in molecules]

        return DatabaseResult(
            source="chembl",
            query=query,
            items=items,
            total_count=len(items),
        )

    async def _search_targets(self, query: str, max_results: int) -> DatabaseResult:
        """Search for targets in ChEMBL."""
        params = {
            "q": query,
            "limit": max_results,
            "format": "json",
        }
        resp = await self._request_with_retry("GET", "/target.json", params=params)
        data = resp.json()
        targets = data.get("targets", data.get("results", []))
        items = [self._parse_target(t) for t in targets]

        return DatabaseResult(
            source="chembl",
            query=query,
            items=items,
            total_count=len(items),
        )

    async def _get_molecule(self, chembl_id: str) -> dict[str, Any]:
        """Fetch a single molecule by ChEMBL ID."""
        resp = await self._request_with_retry("GET", f"/molecule/{chembl_id}.json")
        data = resp.json()
        return self._parse_molecule(data)

    async def _get_target(self, target_id: str) -> dict[str, Any]:
        """Fetch a single target by ChEMBL target ID."""
        resp = await self._request_with_retry("GET", f"/target/{target_id}.json")
        data = resp.json()
        return self._parse_target(data)

    async def _get_assay(self, assay_id: str) -> dict[str, Any]:
        """Fetch a single assay by ChEMBL assay ID."""
        resp = await self._request_with_retry("GET", f"/assay/{assay_id}.json")
        data = resp.json()
        return self._parse_assay(data)

    def _parse_molecule(self, mol: dict) -> dict[str, Any]:
        """Parse a ChEMBL molecule entry."""
        return {
            "chembl_id": mol.get("molecule_chembl_id", ""),
            "pref_name": mol.get("pref_name", ""),
            "synonyms": [s.get("synonym", "") for s in mol.get("molecule_synonyms", [])],
            "molecular_weight": mol.get("molecule_properties", {}).get("mw_freebase", 0),
            "smiles": mol.get("molecule_structures", {}).get("canonical_smiles", ""),
            "inchi": mol.get("molecule_structures", {}).get("standard_inchi", ""),
            "inchi_key": mol.get("molecule_structures", {}).get("standard_inchi_key", ""),
            "alogp": mol.get("molecule_properties", {}).get("alogp", 0),
            "hbd": mol.get("molecule_properties", {}).get("hbd", 0),  # H-bond donors
            "hba": mol.get("molecule_properties", {}).get("hba", 0),  # H-bond acceptors
            "ro5_violations": mol.get("molecule_properties", {}).get("num_ro5_violations", 0),
            "max_phase": mol.get("max_phase", 0),  # Clinical trial phase
            "first_approval": mol.get("first_approval", 0),
            "oral": mol.get("oral", False),
            "parenteral": mol.get("parenteral", False),
            "topical": mol.get("topical", False),
            "source": "ChEMBL",
        }

    def _parse_target(self, target: dict) -> dict[str, Any]:
        """Parse a ChEMBL target entry."""
        return {
            "chembl_id": target.get("target_chembl_id", ""),
            "pref_name": target.get("pref_name", ""),
            "target_type": target.get("target_type", ""),
            "organism": target.get("organism", ""),
            "description": target.get("description", ""),
            "uniprot_id": target.get("target_components", [{}])[0].get("accession", "")
            if target.get("target_components")
            else "",
            "source": "ChEMBL",
        }

    def _parse_assay(self, assay: dict) -> dict[str, Any]:
        """Parse a ChEMBL assay entry."""
        return {
            "chembl_id": assay.get("assay_chembl_id", ""),
            "description": assay.get("description", ""),
            "assay_type": assay.get("assay_type", ""),
            "organism": assay.get("assay_organism", ""),
            "tissue": assay.get("assay_tissue", ""),
            "cell_type": assay.get("assay_cell_type", ""),
            "subcellular_fraction": assay.get("assay_subcellular_fraction", ""),
            "source": "ChEMBL",
        }

    async def get_bioactivities(self, molecule_id: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Fetch bioactivity data for a molecule.

        Args:
            molecule_id: ChEMBL molecule ID (e.g., "CHEMBL25").
            max_results: Maximum results.

        Returns:
            List of bioactivity dicts.
        """
        try:
            params = {
                "molecule_chembl_id": molecule_id,
                "limit": max_results,
                "format": "json",
            }
            resp = await self._request_with_retry("GET", "/activity.json", params=params)
            data = resp.json()
            activities = data.get("activities", [])
            return [
                {
                    "assay_id": a.get("assay_chembl_id", ""),
                    "target_id": a.get("target_chembl_id", ""),
                    "target_name": a.get("target_pref_name", ""),
                    "type": a.get("standard_type", ""),
                    "value": a.get("standard_value", ""),
                    "units": a.get("standard_units", ""),
                    "relation": a.get("standard_relation", ""),
                    "pchembl": a.get("pchembl_value", ""),
                }
                for a in activities
            ]
        except Exception as e:
            # P0 (E6): do NOT return [] on failure — empty bioactivities look
            # like "no activity data" vs a ChEMBL outage. Raise to surface it.
            logger.error("Failed to fetch bioactivities for %s: %s", molecule_id, e)
            raise RuntimeError(f"chembl_fetch_bioactivities_failed: {e}") from e

    async def get_drug_indications(self, molecule_id: str) -> list[dict[str, Any]]:
        """Fetch drug indications for a molecule.

        Args:
            molecule_id: ChEMBL molecule ID.

        Returns:
            List of indication dicts.
        """
        try:
            params = {
                "molecule_chembl_id": molecule_id,
                "format": "json",
            }
            resp = await self._request_with_retry("GET", "/drug_indication.json", params=params)
            data = resp.json()
            indications = data.get("drug_indications", [])
            return [
                {
                    "efo_term": ind.get("efo_term", ""),
                    "mesh_id": ind.get("mesh_id", ""),
                    "max_phase_for_ind": ind.get("max_phase_for_ind", 0),
                }
                for ind in indications
            ]
        except Exception as e:
            # P0 (E6): do NOT return [] on failure — see get_bioactivities note.
            logger.error("Failed to fetch drug indications: %s", e)
            raise RuntimeError(f"chembl_fetch_drug_indications_failed: {e}") from e
