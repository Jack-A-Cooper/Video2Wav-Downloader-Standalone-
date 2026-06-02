"""Filesystem-safe, music-production-friendly output naming helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple


WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_title(title: str, max_len: int = 150) -> str:
    """Remove characters that are invalid or troublesome in Windows filenames."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    if not cleaned:
        return ""
    if cleaned.lower() in WINDOWS_RESERVED:
        cleaned = f"audio_{cleaned}"
    return cleaned[:max_len].rstrip()


def title_looks_sane(title: str) -> bool:
    """Reject empty, meaningless, or ID-like titles before using them as names."""
    cleaned = sanitize_title(title)
    if len(cleaned) < 4:
        return False
    meaningful = re.sub(r"[^A-Za-z0-9]", "", cleaned)
    if len(meaningful) < 4:
        return False
    if re.fullmatch(r"[A-Za-z0-9_-]{18,}", cleaned):
        return False
    return True


def _with_source(title: str, source_name: str) -> str:
    """Attach source context to the title when it is not already present."""
    clean_title = sanitize_title(title, max_len=115)
    clean_source = sanitize_title(source_name, max_len=45)
    if clean_source and clean_source.lower() not in clean_title.lower():
        return f"{clean_title} - from {clean_source}"
    return clean_title


def _next_counter_name(output_dir: Path, ext: str) -> Path:
    """Return the next numeric fallback name in an output directory."""
    existing = set()
    suffix = ext.lstrip(".").lower()
    for item in output_dir.glob(f"*.{suffix}"):
        if item.stem.isdigit():
            existing.add(int(item.stem))
    n = 1
    while n in existing:
        n += 1
    return output_dir / str(n)


def determine_output_stub(output_dir: Path, raw_title: str, source_name: str, ext: str = "wav") -> Tuple[Path, bool]:
    """Choose an output filename stem and report whether a real title was used."""
    if title_looks_sane(raw_title):
        base = _with_source(raw_title, source_name)
        return output_dir / base, True
    return _next_counter_name(output_dir, ext), False


def keep_both_stub(stub: Path, ext: str = "wav") -> Path:
    """Return a numbered sibling stem for non-overwriting duplicate handling."""
    suffix = f".{ext.lstrip('.')}"
    i = 2
    while stub.with_name(f"{stub.name}_{i}").with_suffix(suffix).exists():
        i += 1
    return stub.with_name(f"{stub.name}_{i}")
