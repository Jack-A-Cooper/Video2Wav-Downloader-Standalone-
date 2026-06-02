"""Media discovery pipeline for Video2WAV.

Discovery is deliberately layered:

1. Ask yt-dlp first because it has the broadest extractor coverage.
2. If yt-dlp sees a playlist, expose each playlist entry as a selectable item.
3. If yt-dlp fails or only uses a generic extractor, scan the page DOM for
   direct media URLs, embedded players, Open Graph metadata, and JSON-LD video
   hints.

This module should be the first place to extend when adding platform-specific
fallbacks or custom extractors.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore

from .models import MediaCandidate

try:
    from yt_dlp import YoutubeDL
except ImportError:
    YoutubeDL = None  # type: ignore


DIRECT_MEDIA_RE = re.compile(
    r"\.(mp4|mov|m4v|webm|mkv|avi|wmv|flv|mpg|mpeg|m3u8|mpd)(?:[?#].*)?$",
    re.IGNORECASE,
)


class DiscoveryError(RuntimeError):
    """Raised when no usable media candidate can be found for a URL."""
    pass


def _site_folder(url: str, info: Optional[Dict[str, Any]] = None) -> str:
    """Return a filesystem-safe folder name for grouping outputs by source site."""
    candidates = []
    if info:
        candidates.extend([info.get("extractor_key"), info.get("extractor"), info.get("webpage_url_domain")])
    candidates.append(urlparse(url).hostname)
    for value in candidates:
        if value:
            text = str(value).strip().lower().replace(":", "_").replace(".", "_").replace("-", "_")
            return text[:80]
    return "unknown_site"


def _source_name(url: str, info: Dict[str, Any]) -> str:
    """Pick a human-readable source label from yt-dlp metadata."""
    for key in ("series", "playlist_title", "album", "channel", "uploader", "creator", "webpage_url_domain"):
        value = info.get(key)
        if value:
            return str(value).strip()
    return urlparse(url).hostname or "unknown source"


def _candidate_from_info(info: Dict[str, Any], fallback_url: str, kind: str) -> MediaCandidate:
    """Translate one yt-dlp metadata dictionary into the app's candidate model."""
    url = info.get("webpage_url") or info.get("url") or fallback_url
    title = str(info.get("title") or info.get("fulltitle") or "Untitled video").strip()
    playlist_index = info.get("playlist_index")
    if not isinstance(playlist_index, int):
        playlist_index = None
    return MediaCandidate(
        url=str(url),
        title=title,
        source_name=_source_name(fallback_url, info),
        site_folder=_site_folder(fallback_url, info),
        kind=kind,
        duration=info.get("duration"),
        playlist_title=info.get("playlist_title"),
        playlist_index=playlist_index,
        extractor=str(info.get("extractor") or info.get("extractor_key") or "unknown"),
        format_note=_format_note(info),
    )


def _format_note(info: Dict[str, Any]) -> Optional[str]:
    """Build a short technical hint shown in candidate selection menus."""
    formats = info.get("formats") or []
    if not formats:
        return None
    heights = [fmt.get("height") for fmt in formats if isinstance(fmt.get("height"), int)]
    audio_codecs = sorted({str(fmt.get("acodec")) for fmt in formats if fmt.get("acodec") and fmt.get("acodec") != "none"})
    parts = []
    if heights:
        parts.append(f"max video: {max(heights)}p")
    if audio_codecs:
        parts.append("audio: " + ", ".join(audio_codecs[:4]))
    return "; ".join(parts) if parts else None


def _yt_dlp_extract(url: str, cookies_browser: Optional[str], verbose: bool) -> Dict[str, Any]:
    """Inspect a URL through yt-dlp without downloading media."""
    if YoutubeDL is None:
        raise DiscoveryError("yt-dlp is not installed. Run: pip install -r requirements.txt")

    opts: Dict[str, Any] = {
        "quiet": not verbose,
        "no_warnings": not verbose,
        "skip_download": True,
        "ignoreerrors": True,
        "extract_flat": "in_playlist",
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise DiscoveryError(str(exc)) from exc
    if not info:
        raise DiscoveryError("yt-dlp did not return media information.")
    return info


def _playlist_candidates(info: Dict[str, Any], page_url: str) -> List[MediaCandidate]:
    """Expand a yt-dlp playlist response into selectable media candidates."""
    entries = info.get("entries") or []
    candidates: List[MediaCandidate] = []
    playlist_title = info.get("title") or info.get("playlist_title")
    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        entry_url = entry.get("webpage_url") or entry.get("url")
        if not entry_url:
            continue
        if isinstance(entry_url, str) and not entry_url.startswith(("http://", "https://")):
            ie_key = entry.get("ie_key")
            if ie_key and str(ie_key).lower() == "youtube":
                entry_url = f"https://www.youtube.com/watch?v={entry_url}"
            else:
                entry_url = urljoin(page_url, str(entry_url))
        merged = dict(entry)
        merged["playlist_title"] = playlist_title
        merged["playlist_index"] = entry.get("playlist_index") or idx
        candidates.append(_candidate_from_info(merged, str(entry_url), kind="playlist item"))
    return candidates


def _request_html(url: str) -> Optional[str]:
    """Fetch webpage HTML for fallback DOM analysis."""
    if requests is None:
        raise DiscoveryError("requests is not installed. Run: pip install -r requirements.txt")
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Video2WAV/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        content_type = resp.headers.get("content-type", "").lower()
        if "html" not in content_type and not resp.text.lstrip().startswith(("<!doctype", "<html")):
            return None
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None


def _json_ld_urls(soup: BeautifulSoup, base_url: str) -> Iterable[str]:
    """Yield likely media URLs from JSON-LD structured data blocks."""
    for script in soup.find_all("script", type=lambda value: value and "ld+json" in value):
        text = script.get_text(" ", strip=True)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key in ("contentUrl", "embedUrl", "url"):
                    value = item.get(key)
                    if isinstance(value, str) and value.startswith(("http://", "https://", "/")):
                        yield urljoin(base_url, value)
                for value in item.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(item, list):
                stack.extend(item)


def _dom_candidates(page_url: str) -> List[MediaCandidate]:
    """Find embedded/direct media candidates by inspecting the webpage DOM."""
    if BeautifulSoup is None:
        raise DiscoveryError("beautifulsoup4 is not installed. Run: pip install -r requirements.txt")
    html = _request_html(page_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else "Embedded video"
    urls: List[tuple[str, str]] = []

    selectors = [
        ("video", "src", "video tag"),
        ("source", "src", "source tag"),
        ("a", "href", "direct media link"),
        ("iframe", "src", "embedded frame"),
        ("embed", "src", "embedded object"),
    ]
    for tag_name, attr, kind in selectors:
        for tag in soup.find_all(tag_name):
            value = tag.get(attr)
            if value:
                absolute = urljoin(page_url, unescape(value))
                if kind != "direct media link" or DIRECT_MEDIA_RE.search(absolute):
                    urls.append((absolute, kind))

    for meta_name in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream", "twitter:player"):
        for tag in soup.find_all("meta"):
            prop = tag.get("property") or tag.get("name")
            if prop == meta_name and tag.get("content"):
                urls.append((urljoin(page_url, unescape(tag["content"])), "metadata video"))

    for found in _json_ld_urls(soup, page_url):
        if DIRECT_MEDIA_RE.search(found) or "youtube.com" in found or "vimeo.com" in found:
            urls.append((found, "structured data"))

    seen: Set[str] = set()
    candidates: List[MediaCandidate] = []
    for url, kind in urls:
        if url in seen or not url.startswith(("http://", "https://")):
            continue
        seen.add(url)
        candidates.append(
            MediaCandidate(
                url=url,
                title=page_title,
                source_name=urlparse(page_url).hostname or "embedded page",
                site_folder=_site_folder(page_url),
                kind=kind,
                format_note="DOM-discovered candidate",
            )
        )
    return candidates


def discover_candidates(url: str, cookies_browser: Optional[str] = None, verbose: bool = False) -> List[MediaCandidate]:
    """Return all candidate media items that can be selected for WAV extraction."""
    info: Optional[Dict[str, Any]] = None
    ytdlp_error: Optional[Exception] = None
    try:
        info = _yt_dlp_extract(url, cookies_browser=cookies_browser, verbose=verbose)
    except DiscoveryError as exc:
        ytdlp_error = exc

    candidates: List[MediaCandidate] = []
    if info:
        entries = info.get("entries")
        if info.get("_type") in {"playlist", "multi_video"} and entries:
            candidates.extend(_playlist_candidates(info, url))
        elif info.get("formats") or info.get("url"):
            candidates.append(_candidate_from_info(info, url, kind="yt-dlp media"))

    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower() if info else ""
    should_dom_scan = bool(ytdlp_error or not candidates or extractor in {"generic", "generic:generic"})
    if should_dom_scan and not DIRECT_MEDIA_RE.search(url):
        candidates.extend(_dom_candidates(url))

    deduped: List[MediaCandidate] = []
    seen: Set[str] = set()
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        deduped.append(candidate)

    if deduped:
        return deduped
    if ytdlp_error:
        raise DiscoveryError(str(ytdlp_error))
    raise DiscoveryError("No downloadable video candidates were found.")
