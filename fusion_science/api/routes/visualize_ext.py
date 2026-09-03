from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...visualization.molecule import MoleculeVisualizer
from ...visualization.protein import ProteinVisualizer

logger = logging.getLogger(__name__)
router = APIRouter()


class MoleculeFromSmilesRequest(BaseModel):
    # F-S9: bound strings. SMILES can be long but cap absurd payloads.
    smiles: str = Field(..., max_length=10000)
    width: int = Field(default=400, ge=50, le=4096)
    height: int = Field(default=300, ge=50, le=4096)


class MoleculeFromPdbRequest(BaseModel):
    pdb_id: str = Field(..., max_length=16)
    width: int = Field(default=400, ge=50, le=4096)
    height: int = Field(default=300, ge=50, le=4096)


class ProteinVisualizeRequest(BaseModel):
    pdb_id: str = Field(..., max_length=16)
    style: str = Field(default="cartoon", max_length=64)
    color: str = Field(default="spectrum", max_length=64)
    width: int = Field(default=400, ge=50, le=4096)
    height: int = Field(default=400, ge=50, le=4096)


class ProteinCompareRequest(BaseModel):
    pdb_id_1: str = Field(..., max_length=16)
    pdb_id_2: str = Field(..., max_length=16)
    width: int = Field(default=800, ge=50, le=4096)
    height: int = Field(default=400, ge=50, le=4096)


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
