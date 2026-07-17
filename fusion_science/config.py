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
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScienceConfig:
    """Complete configuration for fusion-science."""

    # Inference engine
    model_name: str = "qwen3.5-9b"
    engine_base_url: str = "http://localhost:8000/v1"
    engine_api_key: str = "local"
    engine_timeout: float = 300.0
    engine_temperature: float = 0.3
    engine_max_tokens: int = 8192

    # Database
    use_mirrors: bool = False
    cache_enabled: bool = True
    cache_dir: str = "~/.cache/fusion-science"
    pubmed_email: str = "research@localhost"

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

    # Audit
    tracing_enabled: bool = True
    trace_dir: str = "~/.cache/fusion-science/traces"
    provenance_dir: str = "~/.cache/fusion-science/provenance"


def load_config(path: str | None = None) -> ScienceConfig:
    """Load configuration from a file, with environment variable overrides.

    Args:
        path: Path to config file (JSON/YAML). If None, searches standard locations.

    Returns:
        ScienceConfig with merged settings.
    """
    config = ScienceConfig()

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
            with open(path, "r") as f:
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

    # Environment variable overrides (FUSION_SCIENCE_*)
    env_prefix = "FUSION_SCIENCE_"
    for key, value in os.environ.items():
        if key.startswith(env_prefix):
            config_key = key[len(env_prefix):].lower()
            if hasattr(config, config_key):
                # Type conversion
                current = getattr(config, config_key)
                if isinstance(current, bool):
                    setattr(config, config_key, value.lower() in ("true", "1", "yes"))
                elif isinstance(current, int):
                    setattr(config, config_key, int(value))
                elif isinstance(current, float):
                    setattr(config, config_key, float(value))
                else:
                    setattr(config, config_key, value)

    return config


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