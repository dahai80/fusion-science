# api/routes/databases.py — GET /api/v1/databases, GET /api/v1/databases/{name}/status
# Importers: api/app.py includes router; called by fusion-studio ScienceBridge
# API: list databases + per-db status check; data schema: {name, display_name, category, mirror_env}
# User instruction: "继续实施下一个阶段"

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

_DATABASES = [
    {"name": "pubmed", "display_name": "PubMed", "category": "literature", "mirror_env": "FUSION_SCI_PUBMED"},
    {"name": "uniprot", "display_name": "UniProt", "category": "protein", "mirror_env": "FUSION_SCI_UNIPROT"},
    {"name": "pdb", "display_name": "PDB", "category": "structure", "mirror_env": "FUSION_SCI_PDB"},
    {"name": "ensembl", "display_name": "Ensembl", "category": "genomics", "mirror_env": "FUSION_SCI_ENSEMBL"},
    {"name": "chembl", "display_name": "ChEMBL", "category": "chemistry", "mirror_env": "FUSION_SCI_CHEMBL"},
    {"name": "ngdc", "display_name": "NGDC", "category": "chinese", "mirror_env": "FUSION_SCI_NGDC"},
    {"name": "cnki", "display_name": "CNKI", "category": "chinese", "mirror_env": "FUSION_SCI_CNKI"},
    {"name": "scidb", "display_name": "ScienceDB", "category": "chinese", "mirror_env": "FUSION_SCI_SCIDB"},
]


@router.get("")
async def list_databases():
    return {"databases": _DATABASES, "total": len(_DATABASES)}


@router.get("/{name}/status")
async def database_status(name: str):
    db_info = next((d for d in _DATABASES if d["name"] == name), None)
    if not db_info:
        return {"error": f"Unknown database: {name}"}
    try:
        connector_map = {
            "pubmed": ("fusion_science.database.pubmed", "PubMedConnector"),
            "uniprot": ("fusion_science.database.uniprot", "UniProtConnector"),
            "pdb": ("fusion_science.database.pdb", "PDBConnector"),
            "ensembl": ("fusion_science.database.ensembl", "EnsemblConnector"),
            "chembl": ("fusion_science.database.chembl", "ChEMBLConnector"),
            "ngdc": ("fusion_science.database.chinese.ngdc", "NGDCConnector"),
            "cnki": ("fusion_science.database.chinese.cnki", "CNKIConnector"),
            "scidb": ("fusion_science.database.chinese.scidb", "ScienceDBConnector"),
        }
        module_path, class_name = connector_map.get(name, (None, None))
        if not module_path:
            return {"name": name, "available": True, "status": "registered", "note": "connector not yet implemented"}
        import importlib

        module = importlib.import_module(module_path)
        connector_cls = getattr(module, class_name)
        connector = connector_cls()
        try:
            healthy = await connector.health_check() if hasattr(connector, "health_check") else True
        finally:
            if hasattr(connector, "close"):
                await connector.close()
        return {
            "name": name,
            "available": True,
            "status": "healthy" if healthy else "degraded",
            "display_name": db_info["display_name"],
        }
    except Exception as e:
        logger.warning("Database status check failed for %s: %s", name, e)
        return {"name": name, "available": False, "status": "error", "error": str(e)}
