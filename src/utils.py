"""Small utility functions shared by Video2WAV modules."""

from __future__ import annotations

import mimetypes
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional


def output_day_str() -> str:
    """Return the local date folder used for grouping generated output."""
    return datetime.now().strftime("%Y-%m-%d")


def eastern_today_str() -> str:
    """Backward-compatible alias for the output day folder.

    Older project versions used US Eastern folders. The current implementation
    intentionally uses local time to avoid requiring IANA timezone data on
    Windows systems that do not have ``tzdata`` installed.
    """
    return output_day_str()


def ensure_dir(path: Path) -> None:
    """Create a directory path if it does not already exist."""
    path.mkdir(parents=True, exist_ok=True)


def friendly_exception(exc: BaseException) -> str:
    """Return user-readable exception text with a class-name fallback."""
    text = str(exc).strip()
    return text or exc.__class__.__name__


def is_probably_text_file(path: Path) -> bool:
    """Return true when a path looks like a URL batch file."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("text/"):
        return True
    return path.suffix.lower() in {".txt", ".list", ".urls"}


def which(name: str) -> Optional[str]:
    """Return an executable path from PATH, or None if it is missing."""
    return shutil.which(name)


def run_command(cmd: List[str], verbose: bool = False, check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess with optional diagnostic echoing."""
    if verbose:
        print("RUN:", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)
