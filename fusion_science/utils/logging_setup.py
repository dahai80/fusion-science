from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)


def configure_file_logging(
    log_path: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    # F-O4: attach a RotatingFileHandler so logs do not grow unbounded across
    # long-running `start.sh` daemons. Disabled when log_path is empty/None.
    # Idempotent: skip if a fusion-science file handler is already attached.
    path = log_path or os.getenv("FUSION_SCIENCE_LOG_FILE", "")
    if not path:
        return
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and getattr(h, "_fs_tag", None) == "fusion-science":
            return
    try:
        handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        handler._fs_tag = "fusion-science"  # type: ignore[attr-defined]
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
        logger.info("File logging enabled: %s (max %d bytes, %d backups)", path, max_bytes, backup_count)
    except Exception as e:
        # Never let log setup crash the server — log to stderr and continue.
        logging.getLogger(__name__).error("Failed to configure file logging at %s: %s", path, e)
