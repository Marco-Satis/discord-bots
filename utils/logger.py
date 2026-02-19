"""
Logging setup with file rotation
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_loggers = {}


def get_logger(name, log_file=None, level=logging.INFO):
    """
    Get or create a logger with console + file output

    Args:
        name: Logger name (usually __name__ or bot name)
        log_file: Optional specific log file path
        level: Log level
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent duplicate handlers
    if logger.handlers:
        _loggers[name] = logger
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler with rotation (10MB, 5 backups)
    if log_file is None:
        safe_name = name.replace(".", "_").replace("/", "_")
        log_file = LOG_DIR / f"{safe_name}.log"

    fh = RotatingFileHandler(
        str(log_file),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _loggers[name] = logger
    return logger
