"""Command-line interaction helpers for Video2WAV.

The GUI reuses several helpers in this module, especially batch parsing and the
range/list selection grammar. Keep this module free of download-specific logic
so user interaction remains separate from media processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from .models import MediaCandidate


@dataclass
class InputItem:
    """One queued input, optionally carrying a user-supplied naming label."""
    url: str
    custom_label: Optional[str] = None


@dataclass
class SessionState:
    """Reserved for cross-item interactive preferences."""
    pass


def print_banner() -> None:
    """Print the command-line welcome banner and basic queue instructions."""
    print("=" * 72)
    print(" Video2WAV")
    print("=" * 72)
    print("Paste URLs one at a time, type 'batch' for a text file, or 'done' to start.")
    print("Batch file format: one URL per line. Optional: URL | custom WAV label")
    print("Playlist/candidate choices accept numbers, ranges, and mixed lists: 1-3,5,7")
    print()


def normalize_user_path(raw: str) -> Optional[Path]:
    """Normalize a user-entered path without requiring that it already exists."""
    if not raw:
        return None
    cleaned = raw.strip().strip('"').strip("'")
    if not cleaned:
        return None
    return Path(cleaned).expanduser()


def parse_batch_file(path: Path) -> List[InputItem]:
    """Read a text batch file into URL queue items.

    Supported lines are either ``URL`` or ``URL | custom label``. Blank lines
    and comment lines beginning with ``#`` are ignored.
    """
    items: List[InputItem] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            url, label = [part.strip() for part in line.split("|", 1)]
            if url:
                items.append(InputItem(url=url, custom_label=label or None))
        else:
            items.append(InputItem(url=line))
    return items


def collect_inputs(batch_file: Optional[Path] = None) -> List[InputItem]:
    """Collect URL inputs interactively or load them from a batch file."""
    if batch_file is not None:
        return parse_batch_file(batch_file)

    items: List[InputItem] = []
    while True:
        raw = input("URL / command: ").strip()
        if not raw:
            continue
        lowered = raw.lower()
        if lowered in {"done", "start", "go"}:
            break
        if lowered in {"quit", "exit"}:
            return []
        if lowered == "batch":
            path = normalize_user_path(input("Path to text file: "))
            if not path or not path.exists() or not path.is_file():
                print("That file could not be found.")
                continue
            loaded = parse_batch_file(path)
            items.extend(loaded)
            print(f"Queued {len(items)} item(s) so far.")
            continue
        if "|" in raw:
            url, label = [part.strip() for part in raw.split("|", 1)]
            items.append(InputItem(url=url, custom_label=label or None))
        else:
            items.append(InputItem(url=raw))
        print(f"Queued {len(items)} item(s). Type 'done' when ready.")
    return items


def _format_duration(seconds: Optional[float]) -> str:
    """Format duration seconds for compact candidate menus."""
    if seconds is None:
        return "duration unknown"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "duration unknown"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _display_candidate(idx: int, candidate: MediaCandidate) -> None:
    """Print a single candidate with enough context to choose accurately."""
    parts = [
        f"{idx}) {candidate.title or 'Untitled'}",
        f"source: {candidate.source_name or candidate.site_folder}",
        _format_duration(candidate.duration),
        f"type: {candidate.kind}",
    ]
    if candidate.playlist_title:
        parts.append(f"playlist: {candidate.playlist_title}")
    if candidate.playlist_index:
        parts.append(f"item: {candidate.playlist_index}")
    if candidate.format_note:
        parts.append(candidate.format_note)
    print("  " + " | ".join(parts))


def parse_selection(raw: str, max_value: int) -> List[int]:
    """Parse selection text such as ``1,3,5-6`` or ``all`` into indexes."""
    text = raw.strip().lower()
    if text in {"all", "*"}:
        return list(range(1, max_value + 1))
    selected: Set[int] = set()
    for token in text.replace(",", " ").split():
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            for value in range(start, end + 1):
                if 1 <= value <= max_value:
                    selected.add(value)
        else:
            value = int(token)
            if 1 <= value <= max_value:
                selected.add(value)
    return sorted(selected)


def prompt_candidate_selection(candidates: Sequence[MediaCandidate], auto_yes: bool = False) -> List[MediaCandidate]:
    """Prompt for one or more candidates when discovery found multiple items."""
    if len(candidates) == 1:
        only = candidates[0]
        print(f"\nSelected: {only.title or only.url}")
        return [only]
    if auto_yes:
        return list(candidates)

    print("\nMultiple video/audio candidates were found.")
    print("Choose one, several, a range, or 'all':")
    for idx, candidate in enumerate(candidates, start=1):
        _display_candidate(idx, candidate)

    while True:
        raw = input("Selection: ").strip()
        try:
            indexes = parse_selection(raw, len(candidates))
        except ValueError:
            indexes = []
        if indexes:
            return [candidates[i - 1] for i in indexes]
        print("Invalid selection. Examples: 1, 1-3, 1,3,5-6, all")


def prompt_duplicate_action(existing_path: Path) -> str:
    """Ask how to handle a duplicate output WAV path."""
    if not existing_path.exists():
        return "overwrite"
    print(f"\nA WAV file already exists: {existing_path.name}")
    print("  1) overwrite")
    print("  2) keep both")
    print("  3) skip")
    choice = input("Choose an option [1-3]: ").strip()
    return {"1": "overwrite", "2": "keep_both", "3": "skip"}.get(choice, "skip")


def confirm_cookie_retry(exc: BaseException) -> Optional[str]:
    """Ask whether to retry extraction with browser cookies."""
    print("\nThe extraction failed.")
    print(f"Reason: {exc}")
    print("Some sites require a browser session. Cookie import can expose login/session access to yt-dlp.")
    print("Use it only for sites you trust, and prefer a browser profile dedicated to downloading.")
    print("Common browser names: chrome, edge, firefox, brave")
    raw = input("Type a browser name to retry with cookies, or press Enter to abort: ").strip().lower()
    return raw or None
