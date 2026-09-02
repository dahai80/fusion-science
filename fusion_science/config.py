"""Configuration management for fusion-science.

Manages YAML/JSON configuration files for:
- MLX inference engine settings
- Database connector settings (mirrors, API keys, caching)
- Compute environment (Python, R, Jupyter, HPC)
- Visualization defaults
- Audit and tracing settings
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Sentinel defaults: when load_config() sees these unchanged values, it tries to
# auto-resolve them from the running fusion-mlx service (settings.json + /api/status).
# Env vars / config files override these before auto-resolve runs, so explicit
# settings always win; auto-resolve only fills in sentinels.
_SENTINEL_MODEL_NAME = "qwen3.5-9b"
_SENTINEL_ENGINE_API_KEY = "local"
_MLX_SETTINGS_PATH = Path.home() / ".fusion-mlx" / "settings.json"
# MLX service-discovery endpoint (probes the MLX engine directly, not the gateway).
# Gateway (:11432) does not expose /api/status; override via FUSION_SCI_MLX_STATUS_URL.
_MLX_STATUS_URL = os.getenv("FUSION_SCI_MLX_STATUS_URL", "http://localhost:11434/api/status")


@dataclass
class ScienceConfig:
    """Complete configuration for fusion-science."""

    # Inference engine — routed through fusion-gateway (:11432) per netlayer plan.
    # Override via FUSION_SCIENCE_ENGINE_BASE_URL to bypass the gateway.
    model_name: str = _SENTINEL_MODEL_NAME
    engine_base_url: str = "http://localhost:11432/v1"
    engine_api_key: str = _SENTINEL_ENGINE_API_KEY
    engine_timeout: float = 300.0
    engine_temperature: float = 0.3
    engine_max_tokens: int = 8192

    # Multi-model roles (F-31)
    model_reasoning: str = ""
    model_summarization: str = ""
    model_code: str = ""

    # API server — default 127.0.0.1 (loopback only). Set FUSION_SCIENCE_API_HOST=0.0.0.0
    # to expose on LAN, but then MUST set FUSION_SCIENCE_API_KEY for auth.
    api_host: str = "127.0.0.1"
    api_port: int = 11462
    # I-1: default CORS is loopback-only, not "*". A wildcard CORS policy on a
    # server bound to 0.0.0.0 lets any origin read API responses cross-site.
    # Widen explicitly via FUSION_SCIENCE_API_CORS_ORIGINS (comma-separated).
    api_cors_origins: list[str] = field(default_factory=lambda: ["http://127.0.0.1", "http://localhost"])

    # Database
    use_mirrors: bool = False
    offline_mode: bool = False  # FUSION_OFFLINE_MODE — force local-only
    cache_enabled: bool = True
    cache_dir: str = "~/.cache/fusion-science"
    pubmed_email: str = "research@localhost"

    # Database mirror URLs (overridable via FUSION_SCI_* env vars)
    pubmed_mirror: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    pdb_mirror: str = "https://data.rcsb.org/rest/v1"
    uniprot_mirror: str = "https://rest.uniprot.org"
    ensembl_mirror: str = "https://useast.ensembl.org"
    chembl_mirror: str = "https://www.ebi.ac.uk/chembl/api/data"

    # Chinese domestic database URLs
    ngdc_url: str = "https://ngdc.cncb.ac.cn"
    cngb_url: str = "https://www.cngb.org"
    cnki_url: str = "https://www.cnki.net"
    sciencedb_url: str = "https://www.scidb.cn"

    # Compute
    python_timeout: int = 120
    r_timeout: int = 120
    hpc_partition: str = ""
    hpc_account: str = ""

    # Visualization
    chart_dpi: int = 300
    chart_width: int = 8
    chart_height: int = 6
    chart_format: str = "png"

    # Session persistence — "sqlite" (default, crash-safe) or "memory" (tests only).
    # Production MUST use sqlite: memory store loses all sessions on restart and has
    # no per-session byte bound, so a long conversation can OOM the process.
    session_store: str = "sqlite"
    session_db_path: str = "~/.cache/fusion-science/sessions.db"
    # Per-session safety bound: cap stored messages + total byte size so one runaway
    # conversation cannot exhaust process memory (MemorySessionStore) or bloat the
    # SQLite row (SQLiteSessionStore). 0 = unlimited (tests).
    session_max_messages: int = 500
    session_max_bytes: int = 8 * 1024 * 1024

    # Audit
    tracing_enabled: bool = True
    trace_dir: str = "~/.cache/fusion-science/traces"
    provenance_dir: str = "~/.cache/fusion-science/provenance"


def load_config(path: str | None = None) -> ScienceConfig:
    """Load configuration from a file, with environment variable overrides.

    Also loads .env file from config/ directory if present (python-dotenv).
    Supports both FUSION_SCIENCE_* (for ScienceConfig fields) and
    FUSION_SCI_* (for database mirror URLs) and FUSION_OFFLINE_MODE.

    Args:
        path: Path to config file (JSON/YAML). If None, searches standard locations.

    Returns:
        ScienceConfig with merged settings.
    """
    config = ScienceConfig()

    # Try to load .env file (optional, requires python-dotenv)
    _try_load_dotenv()

    # Search for config files
    if path is None:
        candidates = [
            Path.cwd() / "fusion-science.yml",
            Path.cwd() / "fusion-science.yaml",
            Path.cwd() / "fusion-science.json",
            Path.home() / ".config" / "fusion-science" / "config.yml",
            Path.home() / ".config" / "fusion-science" / "config.yaml",
            Path.home() / ".config" / "fusion-science" / "config.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                path = str(candidate)
                break

    # Load from file
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                if path.endswith((".yml", ".yaml")):
                    import yaml

                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)

            if data:
                for key, value in data.items():
                    if hasattr(config, key):
                        setattr(config, key, value)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s", path, e)

    # Environment variable overrides
    # Supports both FUSION_SCIENCE_* (for ScienceConfig fields) and
    # FUSION_SCI_* (for database mirror URLs) and FUSION_OFFLINE_MODE
    for key, value in os.environ.items():
        if key == "FUSION_OFFLINE_MODE":
            config.offline_mode = value.lower() in ("true", "1", "yes")
        elif key.startswith("FUSION_SCIENCE_"):
            config_key = key[len("FUSION_SCIENCE_") :].lower()
            if hasattr(config, config_key):
                current = getattr(config, config_key)
                if isinstance(current, bool):
                    setattr(config, config_key, value.lower() in ("true", "1", "yes"))
                elif isinstance(current, int):
                    setattr(config, config_key, int(value))
                elif isinstance(current, float):
                    setattr(config, config_key, float(value))
                elif isinstance(current, list):
                    # I-1: comma-separated env value -> list. A raw string would
                    # otherwise be stored as a single-element list/string and
                    # silently break CORSMiddleware origin matching.
                    setattr(
                        config,
                        config_key,
                        [item.strip() for item in value.split(",") if item.strip()],
                    )
                else:
                    setattr(config, config_key, value)
        elif key.startswith("FUSION_SCI_"):
            # Map FUSION_SCI_* to config fields (e.g. FUSION_SCI_PUBMED_MIRROR -> pubmed_mirror)
            config_key = key[len("FUSION_SCI_") :].lower()
            if hasattr(config, config_key):
                setattr(config, config_key, value)

    # Auto-resolve sentinel defaults from running fusion-mlx (non-fatal).
    # Env vars / config files have already overridden above; this only fills
    # in values that are still the sentinel defaults.
    if config.engine_api_key == _SENTINEL_ENGINE_API_KEY or config.model_name == _SENTINEL_MODEL_NAME:
        _resolve_from_mlx(config)

    return config


def _resolve_from_mlx(config: ScienceConfig) -> None:
    # Resolve sentinel engine_api_key / model_name from the local fusion-mlx
    # service. Any failure is non-fatal: log a warning and keep existing values.
    import httpx

    if config.engine_api_key == _SENTINEL_ENGINE_API_KEY:
        try:
            with open(_MLX_SETTINGS_PATH) as f:
                data = json.load(f)
            key = data.get("auth", {}).get("api_key", "")
            if key:
                config.engine_api_key = key
                logger.info("Auto-resolved engine_api_key from fusion-mlx settings")
        except Exception as e:
            logger.warning("Could not read api_key from %s: %s", _MLX_SETTINGS_PATH, e)

    if config.model_name == _SENTINEL_MODEL_NAME:
        try:
            headers = {"X-Fusion-Route": "fusion-science"}
            if config.engine_api_key and config.engine_api_key != _SENTINEL_ENGINE_API_KEY:
                headers["Authorization"] = f"Bearer {config.engine_api_key}"
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(_MLX_STATUS_URL, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            loaded = data.get("loaded_models", [])
            if isinstance(loaded, list) and loaded:
                config.model_name = loaded[0]
                logger.info("Auto-resolved model_name from fusion-mlx: %s", loaded[0])
        except Exception as e:
            logger.warning("Could not auto-detect model from fusion-mlx: %s", e)


def _try_load_dotenv() -> None:
    """Try to load .env file from config/ directory (optional dependency)."""
    try:
        from dotenv import load_dotenv

        env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
        if env_path.exists():
            load_dotenv(str(env_path))
            logger.info("Loaded .env from %s", env_path)
    except ImportError:
        pass  # python-dotenv not installed, skip


def save_config(config: ScienceConfig, path: str) -> None:
    """Save configuration to a file.

    Args:
        config: The configuration to save.
        path: Output file path (JSON or YAML).
    """
    data = {k: v for k, v in config.__dict__.items() if not k.startswith("_")}

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        if path.endswith((".yml", ".yaml")):
            import yaml

            yaml.dump(data, f, default_flow_style=False)
        else:
            json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Saved config to %s", path)


def create_default_config(path: str | None = None) -> str:
    """Create a default configuration file.

    Args:
        path: Output path (default: ~/.config/fusion-science/config.yml).

    Returns:
        Path to the created config file.
    """
    if path is None:
        path = str(Path.home() / ".config" / "fusion-science" / "config.yml")

    config = ScienceConfig()
    save_config(config, path)
    return path
