"""Output settings, naming templates, and profile persistence for Video2WAV.

The GUI and command-line entry point both use this module so output behavior
stays consistent. The settings model intentionally keeps user-facing choices
simple while still allowing deep organization:

- optional JSON metadata sidecars
- site/date/playlist folder layers
- smart, numbered, template-based, or prompt-driven filenames
- save/load profiles as plain JSON files
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path
from string import Formatter
from typing import Any, Dict, Optional, Tuple

from .models import DownloadResult, MediaCandidate
from .naming import sanitize_title, title_looks_sane
from .utils import eastern_today_str, ensure_dir


NAMING_SMART = "smart"
NAMING_TEMPLATE = "template"
NAMING_ASK = "ask"
NAMING_NUMBERED = "numbered"
NAMING_MODES = {NAMING_SMART, NAMING_TEMPLATE, NAMING_ASK, NAMING_NUMBERED}
DEFAULT_NAME_TEMPLATE = "{title} - from {source}"


@dataclass
class OutputSettings:
    """User-configurable output and metadata behavior."""

    output_root: str
    generate_json: bool = True
    organize_by_site: bool = True
    organize_by_date: bool = True
    organize_by_playlist: bool = False
    naming_mode: str = NAMING_SMART
    name_template: str = DEFAULT_NAME_TEMPLATE
    profile_name: str = "default"

    def normalized(self, default_output_root: Path) -> "OutputSettings":
        """Return a sanitized copy with valid mode and output-root values."""
        output_root = self.output_root.strip() or str(default_output_root)
        naming_mode = self.naming_mode if self.naming_mode in NAMING_MODES else NAMING_SMART
        return OutputSettings(
            output_root=str(Path(output_root).expanduser().resolve()),
            generate_json=bool(self.generate_json),
            organize_by_site=bool(self.organize_by_site),
            organize_by_date=bool(self.organize_by_date),
            organize_by_playlist=bool(self.organize_by_playlist),
            naming_mode=naming_mode,
            name_template=self.name_template.strip() or DEFAULT_NAME_TEMPLATE,
            profile_name=sanitize_profile_name(self.profile_name),
        )


def default_output_root(script_dir: Path) -> Path:
    """Return the project-local default output directory."""
    return script_dir / "downloads"


def profiles_dir(script_dir: Path) -> Path:
    """Return the folder where reusable output profiles are stored."""
    return script_dir / "profiles"


def sanitize_profile_name(name: str) -> str:
    """Convert arbitrary profile text into a safe profile filename stem."""
    cleaned = sanitize_title(name or "default", max_len=80).strip()
    if not cleaned:
        return "default"
    return re.sub(r"\s+", "_", cleaned)


def profile_path(script_dir: Path, profile_name: str) -> Path:
    """Return the JSON path for a named profile."""
    return profiles_dir(script_dir) / f"{sanitize_profile_name(profile_name)}.json"


def save_profile(script_dir: Path, settings: OutputSettings) -> Path:
    """Persist settings as a reusable JSON profile and return the file path."""
    normalized = settings.normalized(default_output_root(script_dir))
    ensure_dir(profiles_dir(script_dir))
    path = profile_path(script_dir, normalized.profile_name)
    path.write_text(json.dumps(asdict(normalized), indent=2), encoding="utf-8")
    return path


def load_profile(script_dir: Path, profile_name: str) -> OutputSettings:
    """Load a named JSON profile from disk."""
    path = profile_path(script_dir, profile_name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return OutputSettings(**payload).normalized(default_output_root(script_dir))


def list_profiles(script_dir: Path) -> list[str]:
    """List available saved profile names without file extensions."""
    folder = profiles_dir(script_dir)
    if not folder.exists():
        return []
    return sorted(path.stem for path in folder.glob("*.json") if path.is_file())


def template_fields(candidate: MediaCandidate, input_url: str, date_text: Optional[str] = None) -> Dict[str, str]:
    """Build sanitized fields available to custom filename templates."""
    date_value = date_text or eastern_today_str()
    playlist_index = str(candidate.playlist_index or "")
    raw_title = candidate.title or candidate.source_name or candidate.site_folder or "audio"
    return {
        "title": sanitize_title(raw_title, max_len=120) or "audio",
        "source": sanitize_title(candidate.source_name or candidate.site_folder, max_len=60) or "source",
        "site": sanitize_title(candidate.site_folder, max_len=60) or "site",
        "playlist": sanitize_title(candidate.playlist_title or "", max_len=80),
        "playlist_index": playlist_index,
        "date": sanitize_title(date_value, max_len=20),
        "kind": sanitize_title(candidate.kind or "video", max_len=40),
        "extractor": sanitize_title(candidate.extractor or "", max_len=60),
        "url_host": sanitize_title(_host_from_url(input_url), max_len=60),
    }


def _host_from_url(url: str) -> str:
    """Extract a rough host label for template use without adding dependencies."""
    match = re.match(r"^[a-z]+://([^/]+)", url.strip(), flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def render_name_template(template: str, candidate: MediaCandidate, input_url: str) -> str:
    """Render and sanitize a user-provided filename template."""
    fields = template_fields(candidate, input_url)
    safe_values = {name: fields.get(name, "") for _, name, _, _ in Formatter().parse(template) if name}
    try:
        rendered = template.format(**safe_values)
    except (KeyError, ValueError):
        rendered = DEFAULT_NAME_TEMPLATE.format(**fields)
    return sanitize_title(rendered, max_len=150)


def build_target_dir(output_root: Path, candidate: MediaCandidate, settings: OutputSettings) -> Path:
    """Build the final output directory from the organization settings."""
    parts = [output_root]
    if settings.organize_by_site:
        parts.append(output_root / sanitize_title(candidate.site_folder, max_len=80))
    target = parts[-1]
    if settings.organize_by_date:
        target = target / eastern_today_str()
    if settings.organize_by_playlist and candidate.playlist_title:
        target = target / sanitize_title(candidate.playlist_title, max_len=100)
    ensure_dir(target)
    return target


def next_numbered_stub(output_dir: Path, ext: str = "wav") -> Path:
    """Return the next numeric output stem in an output directory."""
    suffix = ext.lstrip(".").lower()
    existing = set()
    for item in output_dir.glob(f"*.{suffix}"):
        if item.stem.isdigit():
            existing.add(int(item.stem))
    number = 1
    while number in existing:
        number += 1
    return output_dir / str(number)


def smart_stub(output_dir: Path, title: str, source_name: str, ext: str = "wav") -> Tuple[Path, bool]:
    """Return the existing smart title-plus-source stem used by Video2WAV."""
    if title_looks_sane(title):
        clean_title = sanitize_title(title, max_len=115)
        clean_source = sanitize_title(source_name, max_len=45)
        if clean_source and clean_source.lower() not in clean_title.lower():
            return output_dir / f"{clean_title} - from {clean_source}", True
        return output_dir / clean_title, True
    return next_numbered_stub(output_dir, ext=ext), False


def build_output_stub(
    output_dir: Path,
    candidate: MediaCandidate,
    input_url: str,
    settings: OutputSettings,
    custom_name: Optional[str] = None,
    fallback_title: Optional[str] = None,
    ext: str = "wav",
) -> Tuple[Path, bool]:
    """Choose the final WAV filename stem from the selected naming mode."""
    title = fallback_title or candidate.title
    if settings.naming_mode == NAMING_NUMBERED:
        return next_numbered_stub(output_dir, ext=ext), False
    if settings.naming_mode == NAMING_TEMPLATE:
        rendered = render_name_template(settings.name_template, candidate, input_url)
        return output_dir / (rendered or "audio"), bool(rendered)
    if settings.naming_mode == NAMING_ASK and custom_name:
        rendered = sanitize_title(custom_name, max_len=150)
        if rendered:
            return output_dir / rendered, True
    return smart_stub(output_dir, title, candidate.source_name, ext=ext)


def build_metadata(
    input_url: str,
    candidate: MediaCandidate,
    result: DownloadResult,
    used_title_name: bool,
    settings: OutputSettings,
) -> Dict[str, Any]:
    """Create the JSON-serializable metadata sidecar payload."""
    return {
        "input_url": input_url,
        "processed_url": candidate.url,
        "resolved_media_url": result.resolved_media_url,
        "title": candidate.title,
        "source_name": candidate.source_name,
        "site": candidate.site_folder,
        "playlist_title": candidate.playlist_title,
        "playlist_index": candidate.playlist_index,
        "duration_seconds": candidate.duration,
        "saved_file": result.final_path.name,
        "saved_path": str(result.final_path),
        "metadata_path": str(result.final_path.with_suffix(".json")),
        "used_title_name": used_title_name,
        "download_date_eastern": eastern_today_str(),
        "format_id": result.format_id,
        "original_ext": result.original_ext,
        "audio_codec": result.audio_codec,
        "sample_rate": result.sample_rate,
        "channels": result.channels,
        "wav_codec": "pcm_s24le",
        "output_settings": asdict(settings),
        "notes": "Best available audio stream was preferred; WAV conversion preserves sample rate/channels where ffmpeg can.",
    }
