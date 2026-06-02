"""Shared data models used across discovery, UI, download, and metadata code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MediaCandidate:
    """A selectable media item discovered from a URL, page, or playlist."""
    url: str
    title: str
    source_name: str
    site_folder: str
    kind: str
    duration: Optional[float] = None
    playlist_title: Optional[str] = None
    playlist_index: Optional[int] = None
    extractor: Optional[str] = None
    format_note: Optional[str] = None


@dataclass
class DownloadResult:
    """Information returned after a selected candidate is converted to WAV."""
    final_path: Path
    resolved_media_url: Optional[str]
    format_id: Optional[str]
    original_ext: Optional[str]
    audio_codec: Optional[str]
    sample_rate: Optional[str]
    channels: Optional[int]
