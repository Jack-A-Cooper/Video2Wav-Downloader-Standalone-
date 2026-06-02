"""Metadata sidecar helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def write_metadata_sidecar(wav_path: Path, metadata: Dict[str, Any]) -> Path:
    """Write JSON metadata next to a final WAV output."""
    sidecar = wav_path.with_suffix(".json")
    sidecar.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return sidecar
