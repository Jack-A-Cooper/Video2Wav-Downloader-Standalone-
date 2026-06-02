"""Download and WAV conversion routines.

The downloader prefers the best available audio stream because the final output
is a WAV file for music-production workflows. If a site only exposes combined
video+audio, yt-dlp's ``best`` fallback still lets FFmpeg extract audio from the
downloaded media.
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
from typing import Any, Dict, Optional

try:
    from yt_dlp import YoutubeDL
except ImportError:
    YoutubeDL = None  # type: ignore

from .models import DownloadResult, MediaCandidate
from .naming import keep_both_stub
from .utils import which


class DownloadError(RuntimeError):
    """Raised for dependency, download, or FFmpeg conversion failures."""
    pass


def check_dependencies() -> None:
    """Validate Python packages and FFmpeg tools before starting processing."""
    missing_packages = [
        package
        for package in ("requests", "bs4")
        if importlib.util.find_spec(package) is None
    ]
    if YoutubeDL is None:
        raise DownloadError("yt-dlp is not installed. Run: pip install -r requirements.txt")
    if missing_packages:
        raise DownloadError(
            "Missing Python package(s): "
            + ", ".join(missing_packages)
            + ". Run: pip install -r requirements.txt"
        )
    if not which("ffmpeg"):
        raise DownloadError("ffmpeg was not found in PATH.")
    if not which("ffprobe"):
        raise DownloadError("ffprobe was not found in PATH.")


def _progress_hook(d: Dict[str, Any]) -> None:
    """Render compact yt-dlp progress updates for the command line and GUI log."""
    status = d.get("status")
    if status == "downloading":
        downloaded = d.get("_percent_str") or ""
        speed = d.get("_speed_str") or ""
        eta = d.get("_eta_str") or ""
        print(f"\rDownloading {downloaded.strip()} {speed.strip()} ETA {eta.strip()}".rstrip(), end="")
    elif status == "finished":
        print("\nDownload complete. Converting to WAV...")


def _find_wav(stub: Path) -> Optional[Path]:
    """Locate the WAV file produced by yt-dlp/FFmpeg post-processing."""
    exact = stub.with_suffix(".wav")
    if exact.exists():
        return exact
    matches = sorted(stub.parent.glob(stub.name + "*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _extract_audio_details(info: Dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Extract source audio details from yt-dlp's final metadata payload."""
    requested = info.get("requested_downloads") or []
    candidates = requested if isinstance(requested, list) else []
    candidates.append(info)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        acodec = item.get("acodec")
        if acodec and acodec != "none":
            return (
                str(acodec),
                str(item.get("asr")) if item.get("asr") else None,
                item.get("audio_channels") if isinstance(item.get("audio_channels"), int) else None,
            )
    return None, None, None


def download_candidate_to_wav(
    candidate: MediaCandidate,
    output_stub: Path,
    overwrite: bool,
    cookies_browser: Optional[str] = None,
    verbose: bool = False,
) -> DownloadResult:
    """Download one selected media candidate and convert/extract it to WAV."""
    if YoutubeDL is None:
        raise DownloadError("yt-dlp is not installed. Run: pip install -r requirements.txt")

    output_exists = output_stub.with_suffix(".wav").exists()
    final_stub = output_stub if overwrite or not output_exists else keep_both_stub(output_stub, ext="wav")
    output_template = str(final_stub) + ".%(ext)s"

    opts: Dict[str, Any] = {
        "format": "bestaudio[acodec!=none]/best[acodec!=none]/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "progress_hooks": [_progress_hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "0",
            }
        ],
        "postprocessor_args": ["-acodec", "pcm_s24le"],
    }
    if overwrite:
        opts["overwrites"] = True
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(candidate.url, download=True)
    except Exception as exc:
        raise DownloadError(str(exc)) from exc

    wav_path = _find_wav(final_stub)
    if not wav_path:
        raise DownloadError("Conversion finished, but the WAV output file could not be found.")

    resolved_media_url = None
    requested = info.get("requested_downloads") if isinstance(info, dict) else None
    if isinstance(requested, list):
        for item in requested:
            if isinstance(item, dict) and item.get("url"):
                resolved_media_url = str(item["url"])
                break

    audio_codec, sample_rate, channels = _extract_audio_details(info if isinstance(info, dict) else {})

    return DownloadResult(
        final_path=wav_path,
        resolved_media_url=resolved_media_url,
        format_id=str(info.get("format_id")) if isinstance(info, dict) and info.get("format_id") else None,
        original_ext=str(info.get("ext")) if isinstance(info, dict) and info.get("ext") else None,
        audio_codec=audio_codec,
        sample_rate=sample_rate,
        channels=channels,
    )
