"""
Filesystem helpers used throughout the pipeline.
"""
import shutil
from pathlib import Path

from src.config import PATHS


def ensure_dirs() -> None:
    """Create all required runtime directories if they do not exist."""
    for key in ("temp", "output", "data", "logs"):
        PATHS[key].mkdir(parents=True, exist_ok=True)


def cleanup_temp() -> None:
    """Delete all files inside the temp directory without removing the directory itself."""
    temp_dir: Path = PATHS["temp"]
    if not temp_dir.exists():
        return
    for item in temp_dir.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)
        elif item.is_dir():
            shutil.rmtree(item, ignore_errors=True)


def get_temp_path(filename: str) -> Path:
    """Return an absolute path inside the temp directory."""
    return PATHS["temp"] / filename


def get_output_path(filename: str) -> Path:
    """Return an absolute path inside the output directory."""
    return PATHS["output"] / filename
