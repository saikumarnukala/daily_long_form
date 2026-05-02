"""
Centralised logger factory.
Returns a named logger with both console (INFO) and rotating-file (DEBUG) handlers.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str, log_dir: Path = None) -> logging.Logger:
    """
    Return a configured logger instance.

    Args:
        name:    Logger name (typically __name__ of calling module).
        log_dir: Directory for the rotating log file.  Pass None to skip
                 file logging (e.g. in unit tests).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured – avoid duplicate handlers

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ──────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # ── Rotating file handler ────────────────────────────────────
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "pipeline.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
