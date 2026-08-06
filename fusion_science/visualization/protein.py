"""Protein structure visualization — 3D rendering of protein structures.

Provides tools for visualizing protein 3D structures from PDB data,
with support for highlighting domains, mutations, ligands, and
generating publication-quality structural figures.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProteinVisualization:
    """Result of a protein structure visualization."""

    success: bool
    html_path: str = ""  # Interactive 3D HTML
    image_path: str = ""  # Static image
    error: str = ""
    pdb_id: str = ""
    chain_count: int = 0
    residue_count: int = 0


class ProteinVisualizer:
    """Generates 3D protein structure visualizations.

    Supports:
    - PDB structure rendering with py3Dmol
    - Domain/mutation highlighting
    - Ligand and binding site visualization
    - Publication-quality static images
    """

    def __init__(self):
        self._py3dmol_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        try:
            import py3Dmol  # type: ignore[import-untyped]  # noqa: F401

            self._py3dmol_available = True
        except ImportError:
            self._py3dmol_available = False

    async def visualize(
        self,
        pdb_id: str,
        pdb_content: str = "",
        style: str = "cartoon",
        highlights: list[dict[str, Any]] | None = None,
        show_ligands: bool = True,
    ) -> ProteinVisualization:
        """Generate a 3D protein structure visualization.

        Args:
            pdb_id: PDB ID (e.g., "6M0J" for SARS-CoV-2 spike).
            pdb_content: Optional PDB file content.
            style: Visualization style (cartoon, surface, ribbon).
            highlights: Optional regions to highlight:
                [{"start": 100, "end": 200, "color": "red", "label": "Active site"}]
            show_ligands: Whether to show bound ligands.

        Returns:
            ProteinVisualization with generated files.
        """
        output_dir = tempfile.gettempdir()
        html_path = os.path.join(output_dir, f"{pdb_id}_protein.html")
        os.path.join(output_dir, f"{pdb_id}_protein.png")
        pdb_path = os.path.join(output_dir, f"{pdb_id}.pdb")

        try:
            if not pdb_content:
                import httpx

                pdb_base = os.getenv("FUSION_SCI_PDB_MIRROR", "https://files.rcsb.org")
                if pdb_base.endswith("/rest/v1"):
                    pdb_base = "https://files.rcsb.org"
                resp = httpx.get(f"{pdb_base}/download/{pdb_id}.pdb")
                if resp.status_code != 200:
                    return ProteinVisualization(
                        success=False,
                        error=f"Failed to fetch PDB: {pdb_id}",
                        pdb_id=pdb_id,
                    )
                pdb_content = resp.text

            # Save PDB
            with open(pdb_path, "w") as f:
                f.write(pdb_content)

            # Count chains and residues (basic parsing)
            chains = set()
            residues = set()
            for line in pdb_content.split("\n"):
                if line.startswith("ATOM") or line.startswith("HETATM"):
                    chain = line[21:22].strip()
                    if chain:
                        chains.add(chain)
                    try:
                        resi = int(line[22:26].strip())
                        residues.add(resi)
                    except ValueError:
                        pass

            # Generate interactive 3D HTML
            if self._py3dmol_available:
                self._generate_protein_html(
                    pdb_content,
                    html_path,
                    pdb_id,
                    style,
                    highlights or [],
                    show_ligands,
                )
            else:
                html_path = f"https://www.rcsb.org/3d/view/{pdb_id}"
                if os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes"):
                    html_path = f"file://{pdb_path}"

            return ProteinVisualization(
                success=True,
                html_path=html_path,
                pdb_id=pdb_id,
                chain_count=len(chains),
                residue_count=len(residues),
            )

        except Exception as e:
            logger.error("Protein visualization failed: %s", e)
            return ProteinVisualization(
                success=False,
                error=str(e),
                pdb_id=pdb_id,
            )

    def _generate_protein_html(
        self,
        pdb_content: str,
        html_path: str,
        title: str,
        style: str,
        highlights: list[dict[str, Any]],
        show_ligands: bool,
    ) -> None:
        """Generate interactive 3D HTML for protein structure.

        Args:
            pdb_content: PDB file content.
            html_path: Output HTML path.
            title: Title for the viewer.
            style: Visualization style.
            highlights: Regions to highlight.
            show_ligands: Show ligands.
        """
        import base64

        pdb_b64 = base64.b64encode(pdb_content.encode()).decode()

        # Style configuration
        style_map = {
            "cartoon": "view.setStyle({cartoon:{color:'spectrum'}});",
            "surface": "view.setStyle({cartoon:{color:'white',opacity:0.5}});\nview.addSurface($3Dmol.SurfaceType.VDW, {opacity:0.8});",
            "ribbon": "view.setStyle({cartoon:{color:'spectrum',style:'ribbon'}});",
            "trace": "view.setStyle({line:{}});",
        }
        style_js = style_map.get(style, style_map["cartoon"])

        # Highlight regions
        highlight_js = ""
        for h in highlights:
            start = h.get("start", 0)
            end = h.get("end", 0)
            color = h.get("color", "red")
            label = h.get("label", "")
            if start and end:
                highlight_js += f"""
view.setStyle({{resi: {start}-{end}}}, {{cartoon:{{color:'{color}'}}}});
view.addLabel("{label}", {{position:{{x:0,y:0,z:0}}, fontSize:12, fontColor:'{color}'}});
"""

        # Ligands
        ligand_js = ""
        if show_ligands:
            ligand_js = """
// Show ligands as sticks
view.setStyle({hetflag: true}, {stick:{radius:0.3,colorscheme:'Jmol'}});
"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title} - Fusion Science</title>
    <script src="https://3Dmol.org/build/3Dmol-min.js"></script>
    <style>
        body {{ margin: 0; font-family: Arial, sans-serif; }}
        #viewer {{ width: 100%; height: 100vh; }}
        #info {{
            position: absolute; top: 10px; left: 10px;
            background: rgba(255,255,255,0.9);
            padding: 10px 15px; border-radius: 5px;
            font-size: 14px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        #controls {{
            position: absolute; bottom: 20px; left: 50%;
            transform: translateX(-50%);
            background: rgba(255,255,255,0.9);
            padding: 8px 15px; border-radius: 20px;
            font-size: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
    </style>
</head>
<body>
    <div id="info">PDB: {title}</div>
    <div id="viewer"></div>
    <div id="controls">Drag to rotate | Scroll to zoom | Right-click to pan</div>
    <script>
        let viewer = $3Dmol.createViewer("viewer", {{backgroundColor: "white"}});
        let pdbData = atob("{pdb_b64}");
        viewer.addModel(pdbData, "pdb");
        {style_js}
        {highlight_js}
        {ligand_js}
        viewer.zoomTo();
        viewer.render();
    </script>
</body>
</html>"""

        with open(html_path, "w") as f:
            f.write(html)

    async def compare_structures(
        self,
        pdb_ids: list[str],
        alignment_residues: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a comparative visualization of multiple protein structures.

        Args:
            pdb_ids: List of PDB IDs to compare.
            alignment_residues: Optional alignment residues for each structure.

        Returns:
            HTML file path with the comparative viewer.
        """
        output_dir = tempfile.gettempdir()
        html_path = os.path.join(output_dir, f"comparison_{'_'.join(pdb_ids)}.html")

        try:
            import base64

            import httpx

            pdb_contents = []
            for pdb_id in pdb_ids:
                pdb_base = os.getenv("FUSION_SCI_PDB_MIRROR", "https://files.rcsb.org")
                if pdb_base.endswith("/rest/v1"):
                    pdb_base = "https://files.rcsb.org"
                resp = httpx.get(f"{pdb_base}/download/{pdb_id}.pdb")
                if resp.status_code == 200:
                    pdb_contents.append((pdb_id, resp.text))

            if not pdb_contents:
                return ""

            # Generate comparison HTML
            colors = ["blue", "red", "green", "orange", "purple", "cyan"]
            model_js = ""
            for i, (_pid, content) in enumerate(pdb_contents):
                b64 = base64.b64encode(content.encode()).decode()
                color = colors[i % len(colors)]
                model_js += f"""
viewer.addModel(atob("{b64}"), "pdb", {{keepH:true}});
viewer.setStyle({{model:{i}}}, {{cartoon:{{color:'{color}',opacity:0.7}}}});
"""

            html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Structure Comparison - Fusion Science</title>
    <script src="https://3Dmol.org/build/3Dmol-min.js"></script>
    <style>
        body {{ margin: 0; font-family: Arial, sans-serif; }}
        #viewer {{ width: 100%; height: 100vh; }}
        #legend {{
            position: absolute; top: 10px; right: 10px;
            background: rgba(255,255,255,0.9);
            padding: 10px; border-radius: 5px;
            font-size: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
    </style>
</head>
<body>
    <div id="viewer"></div>
    <div id="legend">
        <b>Structures</b><br>
        {"".join(f'<span style="color:{colors[i % len(colors)]}">&#9632;</span> {pid}<br>' for i, (pid, _) in enumerate(pdb_contents))}
    </div>
    <script>
        let viewer = $3Dmol.createViewer("viewer", {{backgroundColor: "white"}});
        {model_js}
        viewer.zoomTo();
        viewer.render();
    </script>
</body>
</html>"""

            with open(html_path, "w") as f:
                f.write(html)

            return html_path

        except Exception as e:
            logger.error("Structure comparison failed: %s", e)
            return ""

    @staticmethod
    def notable_proteins() -> list[dict[str, str]]:
        """Get a list of notable PDB structures for quick testing.

        Returns:
            List of dicts with pdb_id, name, and description.
        """
        return [
            {"pdb_id": "6M0J", "name": "SARS-CoV-2 Spike", "description": "COVID-19 spike protein RBD-ACE2 complex"},
            {"pdb_id": "1BNA", "name": "B-DNA", "description": "B-DNA dodecamer (classic structure)"},
            {"pdb_id": "4HHB", "name": "Hemoglobin", "description": "Human hemoglobin (deoxy)"},
            {"pdb_id": "1MBO", "name": "Myoglobin", "description": "Sperm whale myoglobin"},
            {"pdb_id": "1CRN", "name": "Crambin", "description": "Small protein, common test structure"},
            {"pdb_id": "2RH1", "name": "Beta2-AR", "description": "Beta2-adrenergic receptor (GPCR)"},
            {"pdb_id": "3V6Z", "name": "Kinesin", "description": "Kinesin motor domain"},
            {"pdb_id": "5C1M", "name": "GFP", "description": "Green fluorescent protein"},
            {"pdb_id": "7KXG", "name": "Cas9", "description": "CRISPR-Cas9 complex"},
            {"pdb_id": "6W4B", "name": "Proteasome", "description": "Human proteasome core particle"},
        ]
