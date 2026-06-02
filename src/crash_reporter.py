"""Crash and error reporting for Video2WAV.

Every report is written as a pair of files under the project-root
``crashlogs`` directory:

* ``.txt`` for plain terminal-friendly diagnostics.
* ``.md`` for structured, color-accented debugging in Markdown viewers.

The reporter avoids dumping full environment variables because those can contain
tokens, browser paths, usernames, or other sensitive data. Add explicit fields
through ``extra`` when a module needs more context.
"""

from __future__ import annotations

import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional


def project_root() -> Path:
    """Return the root folder where crashlogs and user output should live."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def crashlog_dir(root: Optional[Path] = None) -> Path:
    """Return and create the root crashlog directory."""
    target = (root or project_root()) / "crashlogs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_crash_report(
    context: str,
    exc: BaseException,
    extra: Optional[Mapping[str, Any]] = None,
    root: Optional[Path] = None,
) -> tuple[Path, Path]:
    """Write paired text/Markdown reports for an exception.

    ``context`` should name the failing subsystem or operation, for example
    ``download_candidate_to_wav`` or ``installer_dependency_check``. The return
    value is ``(txt_path, md_path)`` so callers can print or display it.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")[:-3]
    safe_context = _safe_filename(context)
    base = f"{timestamp}_{safe_context}"
    folder = crashlog_dir(root)
    txt_path = folder / f"{base}.txt"
    md_path = folder / f"{base}.md"

    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    details = _base_details(context=context, timestamp=timestamp, extra=extra)

    txt_path.write_text(_txt_report(details, exc, trace), encoding="utf-8")
    md_path.write_text(_md_report(details, exc, trace), encoding="utf-8")
    return txt_path, md_path


def install_global_exception_hook(context: str = "unhandled_python_exception") -> None:
    """Install a process-wide hook for uncaught Python exceptions."""

    def _hook(exc_type, exc, tb) -> None:
        """Internal helper that performs the hook step."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        exc.__traceback__ = tb
        txt_path, md_path = write_crash_report(context, exc)
        print(f"\nCrash report written:\n  {txt_path}\n  {md_path}\n", file=sys.stderr)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def _base_details(context: str, timestamp: str, extra: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Collect safe diagnostic fields shared by text and Markdown reports."""
    root = project_root()
    return {
        "timestamp": timestamp,
        "context": context,
        "project_root": str(root),
        "cwd": os.getcwd(),
        "argv": " ".join(sys.argv),
        "python": sys.version.replace("\n", " "),
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "extra": dict(extra or {}),
    }


def _txt_report(details: Mapping[str, Any], exc: BaseException, trace: str) -> str:
    """Render a plain text crash report."""
    lines = [
        "Video2WAV Crash/Error Report",
        "=" * 32,
        "",
        f"Timestamp: {details['timestamp']}",
        f"Context: {details['context']}",
        f"Exception: {exc.__class__.__name__}",
        f"Message: {exc}",
        "",
        "Runtime",
        "-" * 7,
        f"Project root: {details['project_root']}",
        f"Working dir: {details['cwd']}",
        f"Arguments: {details['argv']}",
        f"Python: {details['python']}",
        f"Executable: {details['executable']}",
        f"Platform: {details['platform']}",
        f"Machine: {details['machine']}",
        "",
        "Extra Context",
        "-" * 13,
    ]
    extra = details.get("extra") or {}
    if extra:
        lines.extend(f"{key}: {value}" for key, value in extra.items())
    else:
        lines.append("(none)")
    lines.extend(["", "Traceback", "-" * 9, trace])
    return "\n".join(lines)


def _md_report(details: Mapping[str, Any], exc: BaseException, trace: str) -> str:
    """Render a structured Markdown crash report with color-accented sections."""
    extra = details.get("extra") or {}
    extra_rows = "\n".join(f"| `{_escape_md(str(k))}` | `{_escape_md(str(v))}` |" for k, v in extra.items())
    if not extra_rows:
        extra_rows = "| `(none)` | `(none)` |"

    return f"""# Video2WAV Crash/Error Report

<div style="padding:12px;border-left:6px solid #d64545;background:#2a1111;color:#ffdada;">
<strong>{_escape_html(exc.__class__.__name__)}</strong>: {_escape_html(str(exc))}
</div>

## Summary

| Field | Value |
|---|---|
| Timestamp | `{_escape_md(str(details['timestamp']))}` |
| Context | `{_escape_md(str(details['context']))}` |
| Project Root | `{_escape_md(str(details['project_root']))}` |
| Working Directory | `{_escape_md(str(details['cwd']))}` |
| Arguments | `{_escape_md(str(details['argv']))}` |

## Runtime

| Field | Value |
|---|---|
| Python | `{_escape_md(str(details['python']))}` |
| Executable | `{_escape_md(str(details['executable']))}` |
| Platform | `{_escape_md(str(details['platform']))}` |
| Machine | `{_escape_md(str(details['machine']))}` |

## Extra Context

| Key | Value |
|---|---|
{extra_rows}

## Traceback

```text
{trace.rstrip()}
```
"""


def _safe_filename(value: str) -> str:
    """Return a Windows-safe filename token."""
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned.strip("_")[:80] or "error"


def _escape_md(value: str) -> str:
    """Escape backticks in Markdown table inline-code values."""
    return value.replace("`", "'")


def _escape_html(value: str) -> str:
    """Escape the small amount of HTML used for Markdown color accents."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
