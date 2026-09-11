"""Rotating local logs.

Search terms and extracted document text are never written to the log by
default; debug mode adds verbose diagnostics but still avoids document bodies.
"""

from __future__ import annotations

import logging
import logging.handlers
import os

from .paths import logs_dir

_CONFIGURED = False
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3


def setup_logging(level: str = "INFO") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("filescope")
    if _CONFIGURED:
        logger.setLevel(_resolve_level(level))
        return logger

    logger.setLevel(_resolve_level(level))
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    try:
        path = os.path.join(logs_dir(), "filescope.log")
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _CONFIGURED = True
    return logger


def _resolve_level(level: str) -> int:
    return getattr(logging, str(level).upper(), logging.INFO)


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"filescope.{component}")


def log_exception(logger: logging.Logger, message: str, exc: BaseException, path: str = "") -> None:
    """Log a failure without dumping document content."""
    logger.warning("%s path=%s error=%s: %s", message, path or "-", type(exc).__name__, exc)
    logger.debug("%s", "".join(__import__("traceback").format_exception(type(exc), exc, exc.__traceback__)))
