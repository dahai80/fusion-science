"""3D molecular structure visualization — render molecules and chemical structures.

Provides tools for generating interactive 3D visualizations of
molecular structures using py3Dmol and RDKit, supporting SMILES
parsing, PDB structure rendering, and molecular property display.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MoleculeVisualization:
    """Result of a molecular visualization generation."""

    success: bool
    html_path: str = ""  # Interactive 3D HTML view
    image_path: str = ""  # Static 2D image
    pdb_path: str = ""    # PDB file if applicable
    error: str = ""
    smiles: str = ""
    formula: str = ""
    molecular_weight: float = 0.0


class MoleculeVisualizer:
    """Generates 3D molecular structure visualizations.

    Supports:
    - SMILES string parsing and rendering (via RDKit)
    - PDB structure rendering (via py3Dmol)
    - Interactive 3D HTML export
    - Static 2D image export
    """

    def __init__(self):
        self._rdkit_available = False
        self._py3dmol_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check for optional visualization dependencies."""
        try:
            import rdkit  # type: ignore[import-untyped]  # noqa: F401
            self._rdkit_available = True
        except ImportError:
            self._rdkit_available = False

        try:
            import py3Dmol  # type: ignore[import-untyped]  # noqa: F401
            self._py3dmol_available = True
        except ImportError:
            self._py3dmol_available = False

    async def from_smiles(
        self,
        smiles: str,
        name: str = "molecule",
        show_3d: bool = True,
        render_2d: bool = True,
    ) -> MoleculeVisualization:
        """Generate molecular visualization from a SMILES string.

        Args:
            smiles: SMILES notation of the molecule.
            name: Name for the molecule (used in output filenames).
            show_3d: Generate interactive 3D view.
            render_2d: Generate static 2D image.

        Returns:
            MoleculeVisualization with generated files.
        """
        if not self._rdkit_available:
            logger.info("RDKit unavailable, using 2D fallback for SMILES: %s", smiles)
            return await self.from_smiles_2d_fallback(smiles, name)

        try:
            from rdkit import Chem  # type: ignore[import-untyped]
            from rdkit.Chem import AllChem, Descriptors, Draw  # type: ignore[import-untyped]

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return MoleculeVisualization(
                    success=False,
                    error=f"Invalid SMILES: {smiles}",
                    smiles=smiles,
                )

            # Generate 2D coordinates
            AllChem.Compute2DCoords(mol)

            # Calculate properties
            mw = Descriptors.MolWt(mol)
            formula = Chem.rdMolDescriptors.CalcMolFormula(mol)

            # Output paths
            output_dir = tempfile.gettempdir()
            image_path = os.path.join(output_dir, f"{name}_2d.png")
            html_path = os.path.join(output_dir, f"{name}_3d.html")

            # 2D rendering
            if render_2d:
                img = Draw.MolToImage(mol, size=(600, 400))
                img.save(image_path)

            # 3D structure generation and visualization
            if show_3d:
                mol_3d = Chem.MolFromSmiles(smiles)
                mol_3d = Chem.AddHs(mol_3d)
                AllChem.EmbedMolecule(mol_3d, AllChem.ETKDG())
                AllChem.MMFFOptimizeMolecule(mol_3d)

                # Generate PDB (for py3Dmol)
                pdb_path = os.path.join(output_dir, f"{name}.pdb")
                pdb_block = Chem.MolToPDBBlock(mol_3d)
                with open(pdb_path, "w") as f:
                    f.write(pdb_block)

                # Generate interactive 3D HTML
                if self._py3dmol_available:
                    self._generate_3d_html(pdb_block, html_path, name)
                else:
                    html_path = ""

            return MoleculeVisualization(
                success=True,
                html_path=html_path,
                image_path=image_path if render_2d else "",
                smiles=smiles,
                formula=formula,
                molecular_weight=mw,
            )

        except Exception as e:
            logger.error("Molecule visualization failed: %s", e)
            return MoleculeVisualization(
                success=False,
                error=str(e),
                smiles=smiles,
            )

    async def from_smiles_2d_fallback(
        self,
        smiles: str,
        name: str = "molecule",
    ) -> MoleculeVisualization:
        output_dir = tempfile.gettempdir()
        html_path = os.path.join(output_dir, f"{name}_2d_fallback.html")

        logger.info("Generating 2D fallback HTML for SMILES: %s", smiles)

        smiles_escaped = smiles.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        atom_count = sum(1 for c in smiles if c.isupper())
        bond_chars = smiles.count("=") + smiles.count("#") + smiles.count("/")
        has_ring = any(c.isdigit() for c in smiles)
        has_branch = smiles.count("(")
        has_aromatic = any(c in smiles for c in "cnops")

        features: list[str] = []
        if has_ring:
            features.append("Ring structures detected")
        if has_branch:
            features.append(f"{has_branch} branch(es)")
        if bond_chars:
            features.append(f"{bond_chars} multiple/rotatable bond(s)")
        if has_aromatic:
            features.append("Aromatic atoms present")

        features_html = ""
        if features:
            items = "".join(f"<li>{f}</li>" for f in features)
            features_html = f"""
            <div class="section">
                <h3>Structural Features</h3>
                <ul>{items}</ul>
            </div>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{name} - SMILES Visualization</title>
    <style>
        body {{
            margin: 0;
            padding: 40px;
            font-family: 'Courier New', monospace;
            background: #f5f5f5;
        }}
        .card {{
            max-width: 720px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            padding: 40px;
        }}
        h1 {{
            font-size: 24px;
            color: #1a1a2e;
            margin: 0 0 8px 0;
        }}
        .subtitle {{
            font-size: 14px;
            color: #666;
            margin-bottom: 24px;
        }}
        .smiles-display {{
            font-family: 'Courier New', monospace;
            font-size: 22px;
            background: #eef2f7;
            border-left: 4px solid #4a6fa5;
            padding: 16px 20px;
            border-radius: 4px;
            word-break: break-all;
            letter-spacing: 1px;
            color: #2d3748;
        }}
        .section {{
            margin-top: 24px;
        }}
        .section h3 {{
            font-size: 14px;
            color: #4a6fa5;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin: 0 0 8px 0;
        }}
        .meta-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .meta-table td {{
            padding: 6px 12px;
            border-bottom: 1px solid #eee;
            font-size: 14px;
        }}
        .meta-table td:first-child {{
            color: #666;
            width: 140px;
        }}
        ul {{
            margin: 0;
            padding-left: 20px;
        }}
        li {{
            font-size: 14px;
            color: #444;
            margin-bottom: 4px;
        }}
        .note {{
            margin-top: 24px;
            padding: 12px 16px;
            background: #fff8e1;
            border-left: 4px solid #f9a825;
            border-radius: 4px;
            font-size: 13px;
            color: #5d4037;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{name}</h1>
        <div class="subtitle">SMILES Notation Visualization (Fallback Mode)</div>
        <div class="smiles-display">{smiles_escaped}</div>
        {features_html}
        <div class="section">
            <h3>Quick Stats</h3>
            <table class="meta-table">
                <tr><td>SMILES</td><td>{smiles_escaped}</td></tr>
                <tr><td>Heavy atoms</td><td>~{atom_count}</td></tr>
                <tr><td>Notation type</td><td>SMILES (Simplified Molecular-Input Line-Entry System)</td></tr>
            </table>
        </div>
        <div class="note">
            Note: This is a text-based fallback representation. Install RDKit
            (<code>pip install fusion-science[molecule]</code>) for interactive 2D/3D rendering.
        </div>
    </div>
</body>
</html>"""

        try:
            with open(html_path, "w") as f:
                f.write(html)
            logger.info("2D fallback HTML written to %s", html_path)
        except Exception as e:
            logger.error("Failed to write fallback HTML: %s", e)
            return MoleculeVisualization(
                success=False,
                error=f"Fallback HTML write failed: {e}",
                smiles=smiles,
            )

        return MoleculeVisualization(
            success=True,
            html_path=html_path,
            smiles=smiles,
        )

    async def from_pdb(
        self,
        pdb_id: str,
        pdb_content: str = "",
        style: str = "cartoon",
    ) -> MoleculeVisualization:
        """Generate 3D visualization from a PDB ID or PDB content.

        Args:
            pdb_id: PDB ID or filename.
            pdb_content: Optional PDB file content (if not fetching from RCSB).
            style: Visualization style (cartoon, stick, line, sphere).

        Returns:
            MoleculeVisualization with generated HTML.
        """
        output_dir = tempfile.gettempdir()
        html_path = os.path.join(output_dir, f"{pdb_id}_3d.html")
        pdb_path = os.path.join(output_dir, f"{pdb_id}.pdb")

        try:
            if not pdb_content:
                # Fetch from RCSB with configurable mirror URL

                import httpx
                pdb_base = os.getenv("FUSION_SCI_PDB_MIRROR", "https://files.rcsb.org")
                # Normalize: if the mirror URL is an API endpoint, extract the download host
                if pdb_base.endswith("/rest/v1"):
                    pdb_base = "https://files.rcsb.org"
                resp = httpx.get(f"{pdb_base}/download/{pdb_id}.pdb")
                if resp.status_code != 200:
                    return MoleculeVisualization(
                        success=False,
                        error=f"Failed to fetch PDB: {pdb_id}",
                    )
                pdb_content = resp.text

            # Save PDB file
            with open(pdb_path, "w") as f:
                f.write(pdb_content)

            # Generate 3D HTML
            if self._py3dmol_available:
                self._generate_3d_html(pdb_content, html_path, pdb_id, style=style)
            else:
                html_path = f"https://www.rcsb.org/3d/view/{pdb_id}"  # Fallback to RCSB viewer
                # If offline mode, note that the viewer is unavailable
                if os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes"):
                    html_path = f"file://{pdb_path}"  # Local PDB file fallback

            return MoleculeVisualization(
                success=True,
                html_path=html_path,
                pdb_path=pdb_path,
                pdb_id=pdb_id,
            )

        except Exception as e:
            logger.error("PDB visualization failed: %s", e)
            return MoleculeVisualization(
                success=False,
                error=str(e),
            )

    def _generate_3d_html(
        self,
        pdb_content: str,
        html_path: str,
        title: str,
        style: str = "cartoon",
    ) -> None:
        """Generate an interactive 3D HTML file using py3Dmol.

        Args:
            pdb_content: PDB file content.
            html_path: Output HTML file path.
            title: Title for the viewer.
            style: Visualization style.
        """
        import base64
        pdb_b64 = base64.b64encode(pdb_content.encode()).decode()

        style_config = {
            "cartoon": "view.setStyle({cartoon:{color:'spectrum'}});",
            "stick": "view.setStyle({stick:{radius:0.2,colorscheme:'Jmol'}});",
            "line": "view.setStyle({line:{}});",
            "sphere": "view.setStyle({sphere:{radius:0.5,colorscheme:'Jmol'}});",
        }
        style_js = style_config.get(style, style_config["cartoon"])

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <script src="https://3Dmol.org/build/3Dmol-min.js"></script>
    <style>
        body {{ margin: 0; font-family: Arial, sans-serif; }}
        #viewer {{ width: 100%; height: 100vh; }}
    </style>
</head>
<body>
    <div id="viewer"></div>
    <script>
        let viewer = $3Dmol.createViewer("viewer", {{backgroundColor: "white"}});
        let pdbData = atob("{pdb_b64}");
        viewer.addModel(pdbData, "pdb");
        {style_js}
        viewer.zoomTo();
        viewer.render();
    </script>
</body>
</html>"""

        with open(html_path, "w") as f:
            f.write(html)

    @staticmethod
    def known_drugs() -> list[dict[str, str]]:
        """Get a list of known drugs with their SMILES for quick testing.

        Returns:
            List of dicts with name, smiles, and description.
        """
        return [
            {"name": "Aspirin", "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O", "description": "NSAID"},
            {"name": "Ibuprofen", "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O", "description": "NSAID"},
            {"name": "Paracetamol", "smiles": "CC(=O)NC1=CC=C(C=C1)O", "description": "Analgesic"},
            {"name": "Caffeine", "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "description": "Stimulant"},
            {"name": "Penicillin G", "smiles": "CC1(C(N2C(S1)C(C2=O)NC(=O)CC3=CC=CC=C3)C(=O)O)C", "description": "Antibiotic"},
            {"name": "Morphine", "smiles": "CN1CC[C@@]23[C@@H]4[C@H]1CC5=C2C(=C(C=C5)O)O[C@@H]3[C@H](C=C4)O", "description": "Opioid analgesic"},
            {"name": "Dopamine", "smiles": "C1=CC(=C(C=C1CCN)O)O", "description": "Neurotransmitter"},
            {"name": "Remdesivir", "smiles": "CCC(CC)COC(=O)[C@H](C)[P@@](=O)(O)OC[C@H]1O[C@@](C#N)([C@@H](O)[C@H]1O)N1C=CC2=C1N=CN=C2N", "description": "Antiviral"},
        ]
