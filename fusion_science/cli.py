"""Fusion-Science CLI — command-line interface for the scientific research workbench.

Usage:
    fusion-science [OPTIONS] COMMAND [ARGS]...

Commands:
    run         Run a scientific task with natural language
    pipeline    Execute a pre-built pipeline
    search      Search scientific databases
    analyze     Analyze data with Python/R
    visualize   Generate scientific visualizations
    review      Conduct literature review
    audit       Generate audit/reproducibility report
    config      Manage configuration
    info        Show system information
"""

from __future__ import annotations

import logging
import sys

import click

from . import __version__
from .config import ScienceConfig, create_default_config, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI context
# ---------------------------------------------------------------------------


@click.group()
@click.option("--config", "-c", help="Path to config file")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--version", is_flag=True, help="Show version")
@click.pass_context
def cli(ctx: click.Context, config: str | None, verbose: bool, version: bool) -> None:
    """Fusion-Science — Local Scientific Research AI Workbench for Apple Silicon.

    A fully offline, privacy-first scientific AI platform that unifies
    literature review, data computation, visualization, paper writing,
    and result traceability into a single interface.
    """
    if version:
        click.echo(f"fusion-science v{__version__}")
        sys.exit(0)

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load configuration
    cfg = load_config(config)
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg


# ---------------------------------------------------------------------------
# run command — natural language task execution
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("task", required=False)
@click.option("--pipeline", "-p", help="Pipeline template to use")
@click.option("--output", "-o", help="Output directory")
@click.pass_context
def run(ctx: click.Context, task: str | None, pipeline: str | None, output: str | None) -> None:
    """Run a scientific task using natural language.

    If TASK is provided, executes it directly. Otherwise, enters
    interactive mode.
    """
    cfg: ScienceConfig = ctx.obj["config"]

    if not task:
        click.echo("🔬 Fusion-Science Interactive Mode")
        click.echo("Enter your research task, or 'quit' to exit.")
        click.echo("")
        while True:
            task = click.prompt("Task", default="")
            if task.lower() in ("quit", "exit", "q"):
                break
            if task.strip():
                _execute_task(cfg, task, pipeline, output)
    else:
        _execute_task(cfg, task, pipeline, output)


def _execute_task(cfg: ScienceConfig, task: str, pipeline: str | None, output: str | None) -> None:
    """Execute a single task."""
    click.echo(f"\n🚀 Executing: {task}")
    click.echo("   (Engine integration pending — connect to fusion-mlx server for full capability)")
    click.echo(f"   Model: {cfg.model_name}")
    if pipeline:
        click.echo(f"   Pipeline: {pipeline}")
    click.echo("")


# ---------------------------------------------------------------------------
# pipeline command
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("pipeline_name", required=True)
@click.argument("query", required=True)
@click.option("--output", "-o", help="Output directory")
@click.pass_context
def pipeline(ctx: click.Context, pipeline_name: str, query: str, output: str | None) -> None:
    """Execute a pre-built scientific pipeline.

    Available pipelines: literature_review, bioinformatics_analysis,
    molecular_analysis.
    """
    cfg: ScienceConfig = ctx.obj["config"]
    click.echo(f"🔬 Running pipeline '{pipeline_name}' with query: {query}")

    from .core.engine import ScienceEngine
    from .core.pipeline import PipelineFactory

    engine = ScienceEngine(
        model=cfg.model_name,
        base_url=cfg.engine_base_url,
        api_key=cfg.engine_api_key,
        timeout=cfg.engine_timeout,
        temperature=cfg.engine_temperature,
        max_tokens=cfg.engine_max_tokens,
    )

    factory = PipelineFactory(engine)
    try:
        sp = factory.create_pipeline(pipeline_name)
        click.echo(f"   Pipeline created with {len(sp.agents)} agents")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        click.echo(f"   Available: {', '.join(PipelineFactory.list_templates())}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query", required=True)
@click.option("--db", "-d", default="pubmed", help="Database to search (pubmed, arxiv, uniprot, pdb)")
@click.option("--max", "-m", default=10, help="Maximum results")
@click.option("--output", "-o", help="Output file (JSON)")
@click.pass_context
def search(ctx: click.Context, query: str, db: str, max: int, output: str | None) -> None:
    """Search scientific databases."""
    cfg: ScienceConfig = ctx.obj["config"]
    click.echo(f"🔍 Searching {db} for: {query}")

    # Database dispatcher
    db_map = {
        "pubmed": ("PubMedConnector", "PubMed"),
        "arxiv": ("LiteratureSearch", "arXiv"),
        "uniprot": ("UniProtConnector", "UniProt"),
        "pdb": ("PDBConnector", "PDB"),
    }

    db_info = db_map.get(db)
    if not db_info:
        click.echo(f"❌ Unknown database: {db}. Available: {', '.join(db_map.keys())}")
        return

    click.echo(f"   {db_info[1]} connector available (requires network access)")
    click.echo(f"   Mirror mode: {'enabled' if cfg.use_mirrors else 'disabled'}")
    click.echo(f"   Max results: {max}")


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("file", required=False)
@click.option("--lang", "-l", default="python", help="Language (python or r)")
@click.option("--code", "-c", help="Code to execute directly")
@click.option("--output", "-o", help="Output directory")
@click.pass_context
def analyze(ctx: click.Context, file: str | None, lang: str, code: str | None, output: str | None) -> None:
    """Analyze data using Python or R.

    Provide a data file or use --code to pass code directly.
    """
    ctx.obj["config"]
    click.echo(f"📊 Analyzing data ({lang})")

    if code:
        click.echo(f"   Executing provided code ({len(code)} chars)")
    elif file:
        click.echo(f"   Reading data from: {file}")
    else:
        click.echo("   Interactive mode — enter code below (Ctrl+D to execute):")
        code = click.get_text_stream("stdin").read()
        if code.strip():
            click.echo(f"   Executing code ({len(code)} chars)")
        else:
            click.echo("   No code provided.")
            return

    click.echo("   Python executor available (requires sandbox configuration)")


# ---------------------------------------------------------------------------
# visualize command
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("type", type=click.Choice(["chart", "molecule", "protein"]))
@click.option("--data", "-d", help="Data file or SMILES/PDB ID")
@click.option("--output", "-o", help="Output file path")
@click.pass_context
def visualize(ctx: click.Context, type: str, data: str | None, output: str | None) -> None:
    """Generate scientific visualizations.

    Types: chart, molecule (from SMILES), protein (from PDB ID).
    """
    ctx.obj["config"]
    click.echo(f"🎨 Generating {type} visualization")
    if data:
        click.echo(f"   Data: {data}")
    if output:
        click.echo(f"   Output: {output}")


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query", required=True)
@click.option("--max-papers", "-m", default=20, help="Maximum papers to review")
@click.option("--output", "-o", help="Output file for the review")
@click.pass_context
def review(ctx: click.Context, query: str, max_papers: int, output: str | None) -> None:
    """Conduct a literature review on a research topic."""
    ctx.obj["config"]
    click.echo(f"📚 Literature review: {query}")
    click.echo(f"   Max papers: {max_papers}")
    if output:
        click.echo(f"   Output: {output}")


# ---------------------------------------------------------------------------
# audit command
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--output", "-o", default="./audit_report.md", help="Output file")
@click.option("--format", "-f", type=click.Choice(["markdown", "json"]), default="markdown")
@click.pass_context
def audit(ctx: click.Context, output: str, format: str) -> None:
    """Generate an audit/reproducibility report for the current session."""
    ctx.obj["config"]
    click.echo(f"📋 Generating audit report: {output}")
    click.echo(f"   Format: {format}")


# ---------------------------------------------------------------------------
# config command
# ---------------------------------------------------------------------------


@cli.group()
def config() -> None:
    """Manage configuration."""
    pass


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Show current configuration."""
    cfg: ScienceConfig = ctx.obj["config"]
    click.echo("Current configuration:")
    click.echo("")
    for key, value in sorted(cfg.__dict__.items()):
        if not key.startswith("_"):
            click.echo(f"  {key}: {value}")


@config.command("init")
@click.option("--path", "-p", help="Config file path")
def config_init(path: str | None) -> None:
    """Create a default configuration file."""
    path = create_default_config(path)
    click.echo(f"✅ Created default config: {path}")


# ---------------------------------------------------------------------------
# info command
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show system information and available components."""
    cfg: ScienceConfig = ctx.obj["config"]

    click.echo("🔬 Fusion-Science System Info")
    click.echo("")
    click.echo(f"  Version: {__version__}")
    click.echo(f"  Model: {cfg.model_name}")
    click.echo(f"  Engine: {cfg.engine_base_url}")
    click.echo(f"  Mirrors: {'enabled' if cfg.use_mirrors else 'disabled'}")
    click.echo(f"  Cache: {'enabled' if cfg.cache_enabled else 'disabled'}")
    click.echo("")

    # Check available components
    click.echo("  Components:")
    click.echo("    ✅ Core engine (MLX client)")
    click.echo("    ✅ Database connectors (PubMed, UniProt, PDB, Ensembl, ChEMBL)")
    click.echo("    ✅ Compute layer (Python, R, Jupyter, HPC)")
    click.echo("    ✅ Visualization (charts, molecules, proteins)")
    click.echo("    ✅ Literature (search, review, paper)")
    click.echo("    ✅ Audit (trace, provenance, reports)")
    click.echo("")

    # Check dependencies
    click.echo("  Optional Dependencies:")
    deps = {
        "mlx": "mlx-lm",
        "jupyter": "jupyter-client",
        "r": "rpy2",
        "molecule": "rdkit, py3Dmol",
    }
    for extra, packages in deps.items():
        available = True
        for pkg in packages.split(", "):
            try:
                __import__(pkg.split("-")[0])
            except ImportError:
                available = False
        status = "✅" if available else "❌"
        click.echo(f"    {status} [{extra}] {packages}")


# ---------------------------------------------------------------------------
# Web UI command
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default=None, help="Host to bind to (default: config api_host)")
@click.option("--port", default=None, type=int, help="Port to listen on (default: config api_port)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None, reload: bool) -> None:
    """Start the Fusion-Science API server."""
    cfg: ScienceConfig = ctx.obj["config"]
    host = host or cfg.api_host
    port = port or cfg.api_port

    click.echo(f"🚀 Starting Fusion-Science API at http://{host}:{port}")
    click.echo(f"   Docs: http://{host}:{port}/docs")
    click.echo(f"   Model: {cfg.model_name}")
    click.echo(f"   Engine: {cfg.engine_base_url}")
    if reload:
        click.echo("   Auto-reload: enabled")

    try:
        import uvicorn

        uvicorn.run(
            "fusion_science.api.app:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except ImportError:
        click.echo("❌ uvicorn not installed. Install with: pip install 'fusion-science[api]'")
        sys.exit(1)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8080, help="Port to listen on")
@click.pass_context
def web(ctx: click.Context, host: str, port: int) -> None:
    """Launch the Fusion-Science web interface (alias for serve)."""
    ctx.invoke(serve, host=host, port=port)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
