#!/usr/bin/env python3
"""Command-line and GUI entry point for Video2WAV.

This module intentionally stays thin: it coordinates input collection,
candidate discovery, WAV conversion, and metadata writing while delegating
specialized work to the modules under ``src/``. Keeping the entry point small
makes it easier to swap discovery/downloading/naming behavior without
rewriting the user interface.

Run:
  python video2wav.py
  python video2wav.py --gui
  python video2wav.py "https://example.com/video-page"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from src.crash_reporter import install_global_exception_hook, write_crash_report
from src.discovery import DiscoveryError, discover_candidates
from src.download_core import DownloadError, check_dependencies, download_candidate_to_wav
from src.interactive import (
    InputItem,
    SessionState,
    collect_inputs,
    confirm_cookie_retry,
    normalize_user_path,
    parse_batch_file,
    print_banner,
    prompt_candidate_selection,
    prompt_duplicate_action,
)
from src.metadata import write_metadata_sidecar
from src.security import UrlSafetyError, validate_user_url
from src.settings import (
    NAMING_ASK,
    NAMING_MODES,
    OutputSettings,
    build_metadata,
    build_output_stub,
    build_target_dir,
    default_output_root,
    load_profile,
)
from src.utils import eastern_today_str, ensure_dir, friendly_exception, is_probably_text_file


def parse_args() -> argparse.Namespace:
    """Parse command-line flags shared by script and packaged executable modes."""
    parser = argparse.ArgumentParser(
        description="Extract high-quality WAV audio from video URLs, webpages, and playlists.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Optional URL or .txt batch file. If omitted, interactive mode is used.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the optional Tkinter URL queue GUI.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        dest="cookies_browser",
        default=None,
        help="Optional browser name for yt-dlp cookie import. Use only for trusted sites.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Optional output root. Defaults to downloads inside this project.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Load a saved output/settings profile from profiles/<name>.json.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write JSON metadata sidecars beside finalized WAV files.",
    )
    parser.add_argument(
        "--naming-mode",
        choices=sorted(NAMING_MODES),
        default=None,
        help="Filename strategy: smart, template, ask, or numbered.",
    )
    parser.add_argument(
        "--name-template",
        default=None,
        help="Template used when --naming-mode template is selected. Available fields include {title}, {source}, {site}, {playlist}, {playlist_index}, {date}, {kind}, and {url_host}.",
    )
    parser.add_argument(
        "--flat-output",
        action="store_true",
        help="Write WAVs directly into the output root without site/date folders.",
    )
    parser.add_argument(
        "--no-site-folders",
        action="store_true",
        help="Do not create per-site output folders.",
    )
    parser.add_argument(
        "--no-date-folders",
        action="store_true",
        help="Do not create per-date output folders.",
    )
    parser.add_argument(
        "--playlist-folders",
        action="store_true",
        help="Create a playlist-title folder when processing playlist items.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show extra diagnostic output.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Use defaults where safe. Duplicate files are kept with a numbered suffix.",
    )
    return parser.parse_args()


def expand_initial_input(raw: Optional[str]) -> List[InputItem]:
    """Convert a positional URL or batch-file path into normalized queue items."""
    if not raw:
        return []
    candidate = normalize_user_path(raw)
    if candidate and candidate.exists() and candidate.is_file() and is_probably_text_file(candidate):
        return parse_batch_file(candidate)
    return [InputItem(url=raw)]


def process_one_input(
    item: InputItem,
    script_dir: Path,
    output_settings: OutputSettings,
    session: SessionState,
    cookies_browser: Optional[str] = None,
    verbose: bool = False,
    auto_yes: bool = False,
) -> None:
    """Process one queued input URL from discovery through final WAV metadata.

    The same function is used for direct URLs, webpage URLs, and playlist URLs.
    ``discover_candidates`` decides whether the input maps to one media item or
    a selectable list of items. The user-facing duplicate/cookie prompts live in
    ``interactive.py`` so this function can focus on orchestration.
    """
    safe_url = validate_user_url(item.url)

    try:
        candidates = discover_candidates(
            safe_url,
            cookies_browser=cookies_browser,
            verbose=verbose,
        )
    except DiscoveryError as exc:
        print(f"\nCould not inspect that URL: {friendly_exception(exc)}")
        write_crash_report(
            "cmd_discovery_error",
            exc,
            extra={"url": safe_url, "cookies_browser": cookies_browser or "(none)"},
        )
        if cookies_browser:
            return
        browser = confirm_cookie_retry(exc)
        if not browser:
            return
        candidates = discover_candidates(safe_url, cookies_browser=browser, verbose=verbose)
        cookies_browser = browser

    selected = prompt_candidate_selection(candidates, auto_yes=auto_yes)
    if not selected:
        print("No candidates selected. Skipping.")
        return

    for candidate in selected:
        output_root = Path(output_settings.output_root).expanduser().resolve()
        target_dir = build_target_dir(output_root, candidate, output_settings)

        title_hint = item.custom_label or candidate.title
        custom_name = prompt_output_name(candidate.title or title_hint) if output_settings.naming_mode == NAMING_ASK else None
        output_stub, used_title = build_output_stub(
            output_dir=target_dir,
            candidate=candidate,
            input_url=safe_url,
            settings=output_settings,
            custom_name=custom_name,
            fallback_title=title_hint,
        )

        duplicate_action = "keep_both" if auto_yes else prompt_duplicate_action(output_stub.with_suffix(".wav"))
        if duplicate_action == "skip":
            print(f"Skipping existing file: {output_stub.with_suffix('.wav').name}")
            continue

        try:
            result = download_candidate_to_wav(
                candidate=candidate,
                output_stub=output_stub,
                overwrite=(duplicate_action == "overwrite"),
                cookies_browser=cookies_browser,
                verbose=verbose,
            )
        except DownloadError as exc:
            print(f"\nDownload/conversion failed: {friendly_exception(exc)}")
            write_crash_report(
                "cmd_download_error",
                exc,
                extra={
                    "input_url": safe_url,
                    "candidate_url": candidate.url,
                    "candidate_title": candidate.title,
                    "output_stub": str(output_stub),
                },
            )
            if cookies_browser:
                continue
            browser = confirm_cookie_retry(exc)
            if not browser:
                continue
            result = download_candidate_to_wav(
                candidate=candidate,
                output_stub=output_stub,
                overwrite=(duplicate_action == "overwrite"),
                cookies_browser=browser,
                verbose=verbose,
            )

        sidecar = None
        if output_settings.generate_json:
            metadata = build_metadata(safe_url, candidate, result, used_title, output_settings)
            sidecar = write_metadata_sidecar(result.final_path, metadata)

        print("\nDone.")
        print(f"Saved: {result.final_path}")
        if sidecar:
            print(f"Metadata: {sidecar}")
        else:
            print("Metadata: disabled")


def run_queue(
    inputs: List[InputItem],
    script_dir: Path,
    output_settings: OutputSettings,
    cookies_browser: Optional[str],
    verbose: bool,
    auto_yes: bool,
) -> int:
    """Run all queued inputs and keep later items moving after isolated failures."""
    session = SessionState()
    if not inputs:
        print("No URLs provided. Exiting.")
        return 0

    for item in inputs:
        try:
            process_one_input(
                item,
                script_dir=script_dir,
                output_settings=output_settings,
                session=session,
                cookies_browser=cookies_browser,
                verbose=verbose,
                auto_yes=auto_yes,
            )
        except UrlSafetyError as exc:
            print(f"\nRejected URL for safety reasons: {exc}")
            write_crash_report("cmd_url_safety_error", exc, extra={"url": item.url})
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            return 130
        except Exception as exc:
            print(f"\nUnexpected error while processing URL: {friendly_exception(exc)}")
            txt_path, md_path = write_crash_report(
                "cmd_unexpected_queue_error",
                exc,
                extra={"url": item.url},
            )
            print(f"Crash report written:\n  {txt_path}\n  {md_path}")

    print("\nAll queued inputs have been processed.")
    return 0


def prompt_output_name(default_name: str) -> Optional[str]:
    """Prompt CMD users for a final WAV filename stem when Ask mode is enabled."""
    try:
        value = input(f"Final WAV name without extension (Enter for smart name: {default_name}): ").strip()
    except EOFError:
        return None
    return value or None


def main() -> int:
    """Application entry point used by Python, batch launchers, and PyInstaller."""
    install_global_exception_hook()
    args = parse_args()
    script_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    output_settings = build_output_settings_from_args(args, script_dir)

    if args.gui:
        from src.gui import launch_gui

        return launch_gui(script_dir=script_dir)

    print_banner()
    try:
        check_dependencies()
    except DownloadError as exc:
        print(f"Dependency check failed: {friendly_exception(exc)}")
        txt_path, md_path = write_crash_report("cmd_dependency_check_error", exc)
        print(f"Crash report written:\n  {txt_path}\n  {md_path}")
        return 1

    inputs = expand_initial_input(args.input)
    if not inputs:
        inputs = collect_inputs()

    return run_queue(
        inputs=inputs,
        script_dir=script_dir,
        output_settings=output_settings,
        cookies_browser=args.cookies_browser,
        verbose=args.verbose,
        auto_yes=args.yes,
    )


def build_output_settings_from_args(args: argparse.Namespace, script_dir: Path) -> OutputSettings:
    """Merge profile and command-line output options into one settings object."""
    if args.profile:
        try:
            settings = load_profile(script_dir, args.profile)
        except Exception as exc:
            print(f"Could not load profile '{args.profile}': {friendly_exception(exc)}")
            settings = OutputSettings(output_root=str(default_output_root(script_dir)))
    else:
        settings = OutputSettings(output_root=str(default_output_root(script_dir)))

    if args.output_root:
        settings.output_root = str(Path(args.output_root).expanduser().resolve())
    if args.no_json:
        settings.generate_json = False
    if args.naming_mode:
        settings.naming_mode = args.naming_mode
    if args.name_template:
        settings.name_template = args.name_template
    if args.flat_output:
        settings.organize_by_site = False
        settings.organize_by_date = False
        settings.organize_by_playlist = False
    else:
        if args.no_site_folders:
            settings.organize_by_site = False
        if args.no_date_folders:
            settings.organize_by_date = False
        if args.playlist_folders:
            settings.organize_by_playlist = True
    return settings.normalized(default_output_root(script_dir))


if __name__ == "__main__":
    raise SystemExit(main())
