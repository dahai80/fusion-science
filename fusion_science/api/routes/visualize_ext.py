from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...visualization.molecule import MoleculeVisualizer
from ...visualization.protein import ProteinVisualizer

logger = logging.getLogger(__name__)
router = APIRouter()


class MoleculeFromSmilesRequest(BaseModel):
    smiles: str
    width: int = 400
    height: int = 300


class MoleculeFromPdbRequest(BaseModel):
    pdb_id: str
    width: int = 400
    height: int = 300


class ProteinVisualizeRequest(BaseModel):
    pdb_id: str
    style: str = "cartoon"
    color: str = "spectrum"
    width: int = 400
    height: int = 400


class ProteinCompareRequest(BaseModel):
    pdb_id_1: str
    pdb_id_2: str
    width: int = 800
    height: int = 400


@router.post("/molecule/smiles")
async def visualize_molecule_smiles(request: Request, body: MoleculeFromSmilesRequest):
    viz = MoleculeVisualizer()
    try:
        result = await viz.from_smiles(body.smiles, width=body.width, height=body.height)
        return result if isinstance(result, dict) else {"html": str(result), "smiles": body.smiles}
    except ImportError:
        result = await viz.from_smiles_2d_fallback(body.smiles)
        return result if isinstance(result, dict) else {"html": str(result), "smiles": body.smiles, "fallback": True}
    except Exception as e:
        logger.error("Molecule viz failed: %s", e)
        return {"error": str(e), "smiles": body.smiles}


@router.post("/molecule/pdb")
async def visualize_molecule_pdb(request: Request, body: MoleculeFromPdbRequest):
    viz = MoleculeVisualizer()
    try:
        result = await viz.from_pdb(body.pdb_id, width=body.width, height=body.height)
        return result if isinstance(result, dict) else {"html": str(result), "pdb_id": body.pdb_id}
    except Exception as e:
        logger.error("Molecule PDB viz failed: %s", e)
        return {"error": str(e), "pdb_id": body.pdb_id}


@router.post("/protein")
async def visualize_protein(request: Request, body: ProteinVisualizeRequest):
    viz = ProteinVisualizer()
    try:
        result = await viz.visualize(
            body.pdb_id, style=body.style, color=body.color, width=body.width, height=body.height
        )
        return result if isinstance(result, dict) else {"html": str(result), "pdb_id": body.pdb_id}
    except Exception as e:
        logger.error("Protein viz failed: %s", e)
        return {"error": str(e), "pdb_id": body.pdb_id}


@router.post("/protein/compare")
async def compare_proteins(request: Request, body: ProteinCompareRequest):
    viz = ProteinVisualizer()
    try:
        result = await viz.compare_structures(body.pdb_id_1, body.pdb_id_2, width=body.width, height=body.height)
        return result if isinstance(result, dict) else {"html": str(result)}
    except Exception as e:
        logger.error("Protein compare failed: %s", e)
        return {"error": str(e)}
