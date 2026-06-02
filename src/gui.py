"""Tkinter GUI for Video2WAV.

The GUI is intentionally a front end over the same core modules used by the
command-line workflow. Processing runs on a worker thread so Tkinter remains
responsive, while UI prompts are marshalled back to the main thread through a
small queue. This pattern keeps downloads/conversions from freezing the window
without calling Tkinter APIs from the wrong thread.
"""

from __future__ import annotations

import contextlib
import io
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from tkinter import (
    BOTH,
    BOTTOM,
    DISABLED,
    END,
    HORIZONTAL,
    LEFT,
    NORMAL,
    RIGHT,
    TOP,
    X,
    Y,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    Listbox,
    Scrollbar,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
    ttk,
)
from typing import Callable, List, Optional, Sequence, TypeVar

from .crash_reporter import write_crash_report
from .discovery import DiscoveryError, discover_candidates
from .download_core import DownloadError, check_dependencies, download_candidate_to_wav
from .interactive import InputItem, parse_batch_file, parse_selection
from .metadata import write_metadata_sidecar
from .models import MediaCandidate
from .security import UrlSafetyError, validate_user_url
from .settings import (
    DEFAULT_NAME_TEMPLATE,
    NAMING_ASK,
    NAMING_MODES,
    OutputSettings,
    build_metadata,
    build_output_stub,
    build_target_dir,
    default_output_root,
    list_profiles,
    load_profile,
    save_profile,
)
from .utils import ensure_dir, friendly_exception


T = TypeVar("T")


@dataclass
class GuiQueueItem:
    """Queue snapshot item used by the GUI worker thread.

    ``row_text`` preserves the exact listbox row so dynamic queue clearing can
    remove only the processed row, even when labels or batch syntax are used.
    """

    input_item: InputItem
    row_text: str


class QueueWriter(io.TextIOBase):
    """Redirect stdout/stderr text from worker threads into the GUI log queue."""

    def __init__(self, log_queue: "queue.Queue[str]") -> None:
        """Store the GUI log queue that receives redirected text."""
        self.log_queue = log_queue

    def writable(self) -> bool:
        """Perform the writable step for the Video2WAV workflow."""
        return True

    def write(self, text: str) -> int:
        """Perform the write step for the Video2WAV workflow."""
        if text:
            self.log_queue.put(text)
        return len(text)


def _duration_text(seconds: Optional[float]) -> str:
    """Format seconds for candidate list labels."""
    if seconds is None:
        return "duration unknown"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _candidate_label(index: int, candidate: MediaCandidate) -> str:
    """Build the one-line label shown for selectable media candidates."""
    parts = [
        f"{index}. {candidate.title or 'Untitled'}",
        f"source: {candidate.source_name or candidate.site_folder}",
        _duration_text(candidate.duration),
        f"type: {candidate.kind}",
    ]
    if candidate.playlist_title:
        parts.append(f"playlist: {candidate.playlist_title}")
    if candidate.playlist_index:
        parts.append(f"item: {candidate.playlist_index}")
    if candidate.format_note:
        parts.append(candidate.format_note)
    return " | ".join(parts)


class Video2WAVGui:
    """Main Video2WAV GUI controller.

    The class owns widget creation, queue management, terminal-command handling,
    and worker-thread orchestration. Media extraction still happens in the core
    discovery/download modules so the GUI stays easy to modify independently.
    """

    def __init__(self, root: Tk, script_dir: Path) -> None:
        """Initialize GUI state, widgets, queues, and worker coordination."""
        self.root = root
        self.script_dir = script_dir
        self.output_root = StringVar(value=str(default_output_root(script_dir)))
        self.cookie_browser = StringVar(value="")
        self.auto_keep_both = BooleanVar(value=False)
        self.static_queue = BooleanVar(value=False)
        self.generate_json = BooleanVar(value=True)
        self.organize_by_site = BooleanVar(value=True)
        self.organize_by_date = BooleanVar(value=True)
        self.organize_by_playlist = BooleanVar(value=False)
        self.naming_mode = StringVar(value="smart")
        self.name_template = StringVar(value=DEFAULT_NAME_TEMPLATE)
        self.profile_name = StringVar(value="default")
        self.verbose = BooleanVar(value=False)
        self.single_url = StringVar(value="")
        self.command_text = StringVar(value="")
        self.status_text = StringVar(value="Queue: 0 | Idle")
        self.processing_state = "Idle"
        self.terminal_visible = BooleanVar(value=True)
        self.last_output_path: Optional[Path] = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.ui_queue: "queue.Queue[tuple[Callable[..., T], tuple, dict, queue.Queue[T]]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None

        self.root.title("Video2WAV")
        self.root.geometry("980x720")
        self.root.minsize(820, 560)
        self.root.configure(bg="#111316")
        self._build()
        self._poll_logs()
        self._poll_ui_requests()

    def _build(self) -> None:
        """Create and lay out all Tkinter widgets."""
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=8)
        style.configure("Accent.TButton", padding=8, background="#2f7d6d", foreground="white")
        style.configure("Danger.TButton", padding=8, background="#8b3a3a", foreground="white")

        shell = Frame(self.root, bg="#111316")
        shell.pack(fill=BOTH, expand=True)

        header = Frame(shell, bg="#191d22")
        header.pack(side=TOP, fill=X)
        Label(header, text="Video2WAV", fg="#f4f1e8", bg="#191d22", font=("Segoe UI", 18, "bold")).pack(side=LEFT, padx=14, pady=10)
        Button(header, text="Toggle Terminal", command=self.toggle_terminal).pack(side=RIGHT, padx=12, pady=10)

        body = Frame(shell, bg="#111316")
        body.pack(fill=BOTH, expand=True, padx=12, pady=10)

        input_frame = Frame(body, bg="#111316")
        input_frame.pack(side=TOP, fill=X)
        Label(input_frame, text="Single URL", fg="#d9d4c7", bg="#111316").pack(anchor="w")
        single_row = Frame(input_frame, bg="#111316")
        single_row.pack(fill=X, pady=(4, 8))
        Entry(single_row, textvariable=self.single_url).pack(side=LEFT, fill=X, expand=True)
        Button(single_row, text="Add URL", command=self.add_single_url).pack(side=LEFT, padx=(8, 0))

        queue_label_row = Frame(input_frame, bg="#111316")
        queue_label_row.pack(fill=X)
        Label(queue_label_row, text="Queue", fg="#d9d4c7", bg="#111316").pack(side=LEFT)
        Label(queue_label_row, textvariable=self.status_text, fg="#9fb2aa", bg="#111316").pack(side=RIGHT)
        queue_frame = Frame(input_frame, bg="#111316")
        queue_frame.pack(fill=BOTH, expand=False)
        self.queue_list = Listbox(queue_frame, height=8, activestyle="dotbox")
        self.queue_list.pack(side=LEFT, fill=BOTH, expand=True)
        queue_scroll = Scrollbar(queue_frame, orient="vertical", command=self.queue_list.yview)
        queue_scroll.pack(side=RIGHT, fill=Y)
        self.queue_list.configure(yscrollcommand=queue_scroll.set)

        queue_buttons = Frame(input_frame, bg="#111316")
        queue_buttons.pack(fill=X, pady=8)
        Button(queue_buttons, text="Load Batch", command=self.load_batch).pack(side=LEFT)
        Button(queue_buttons, text="Remove Selected", command=self.remove_selected).pack(side=LEFT, padx=8)
        Button(queue_buttons, text="Clear Queue", command=self.clear_queue).pack(side=LEFT)
        Button(queue_buttons, text="Open Output", command=self.open_output_folder).pack(side=LEFT, padx=8)
        Button(queue_buttons, text="Open Last WAV", command=self.open_last_output).pack(side=LEFT, padx=(8, 0))
        Button(queue_buttons, text="Copy Last Path", command=self.copy_last_output_path).pack(side=LEFT, padx=8)
        Button(queue_buttons, text="Open Crashlogs", command=self.open_crashlogs).pack(side=LEFT)
        Button(queue_buttons, text="Start Processing", command=self.start_processing).pack(side=RIGHT)

        settings_tabs = ttk.Notebook(body)
        settings_tabs.pack(side=TOP, fill=X, pady=(4, 10))
        output_tab = Frame(settings_tabs, bg="#161a1f", padx=10, pady=10)
        naming_tab = Frame(settings_tabs, bg="#161a1f", padx=10, pady=10)
        profiles_tab = Frame(settings_tabs, bg="#161a1f", padx=10, pady=10)
        settings_tabs.add(output_tab, text="Output")
        settings_tabs.add(naming_tab, text="Organization & Naming")
        settings_tabs.add(profiles_tab, text="Profiles")
        self._build_output_tab(output_tab)
        self._build_naming_tab(naming_tab)
        self._build_profiles_tab(profiles_tab)

        self.terminal_frame = Frame(shell, bg="#090b0d")
        self.terminal_frame.pack(side=BOTTOM, fill=BOTH)
        terminal_header = Frame(self.terminal_frame, bg="#0f1215")
        terminal_header.pack(fill=X)
        Label(terminal_header, text="Terminal", fg="#cdd7d0", bg="#0f1215", font=("Consolas", 10, "bold")).pack(side=LEFT, padx=8, pady=4)
        Label(terminal_header, text="commands: add <url>, batch <path>, start, output <path>, json on/off, profile-save/load <name>, profiles, status, help", fg="#87948e", bg="#0f1215").pack(side=LEFT, padx=8)
        Button(terminal_header, text="Clear Terminal", command=self.clear_terminal).pack(side=RIGHT, padx=8, pady=3)
        self.terminal = Text(self.terminal_frame, height=11, bg="#050607", fg="#d6f5df", insertbackground="#d6f5df", font=("Consolas", 10), wrap="word")
        self.terminal.pack(fill=BOTH, expand=True)
        command_row = Frame(self.terminal_frame, bg="#0f1215")
        command_row.pack(fill=X)
        Label(command_row, text=">", fg="#d6f5df", bg="#0f1215", font=("Consolas", 10, "bold")).pack(side=LEFT, padx=(8, 4))
        command_entry = Entry(command_row, textvariable=self.command_text, bg="#11161a", fg="#f4f1e8", insertbackground="#f4f1e8")
        command_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 8), pady=6)
        command_entry.bind("<Return>", lambda _event: self.run_terminal_command())
        Button(command_row, text="Run", command=self.run_terminal_command).pack(side=RIGHT, padx=8)

        self.log("Video2WAV GUI ready.\n")
        self.refresh_profiles()
        self.update_status()

    def _build_output_tab(self, tab: Frame) -> None:
        """Build output destination and processing option controls."""
        Label(tab, text="Final WAV Directory", fg="#d9d4c7", bg="#161a1f").pack(anchor="w")
        output_row = Frame(tab, bg="#161a1f")
        output_row.pack(fill=X, pady=(4, 8))
        Entry(output_row, textvariable=self.output_root).pack(side=LEFT, fill=X, expand=True)
        Button(output_row, text="Browse", command=self.browse_output).pack(side=LEFT, padx=(8, 0))
        Button(output_row, text="Reset Default", command=self.reset_output_default).pack(side=LEFT, padx=(8, 0))

        option_row = Frame(tab, bg="#161a1f")
        option_row.pack(fill=X, pady=(4, 0))
        Label(option_row, text="Cookie Browser", fg="#d9d4c7", bg="#161a1f").pack(side=LEFT)
        Entry(option_row, textvariable=self.cookie_browser, width=18).pack(side=LEFT, padx=8)
        Checkbutton(option_row, text="Generate JSON metadata", variable=self.generate_json, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=8)
        Checkbutton(option_row, text="Verbose", variable=self.verbose, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=8)
        Checkbutton(option_row, text="Auto keep duplicates", variable=self.auto_keep_both, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=8)
        Checkbutton(option_row, text="Static Queue", variable=self.static_queue, command=self.update_status, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=8)

    def _build_naming_tab(self, tab: Frame) -> None:
        """Build output organization and filename strategy controls."""
        folder_row = Frame(tab, bg="#161a1f")
        folder_row.pack(fill=X)
        Label(folder_row, text="Folder Organization", fg="#d9d4c7", bg="#161a1f").pack(side=LEFT)
        Checkbutton(folder_row, text="Site", variable=self.organize_by_site, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=10)
        Checkbutton(folder_row, text="Date", variable=self.organize_by_date, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=10)
        Checkbutton(folder_row, text="Playlist", variable=self.organize_by_playlist, bg="#161a1f", fg="#d9d4c7", selectcolor="#111316").pack(side=LEFT, padx=10)

        naming_row = Frame(tab, bg="#161a1f")
        naming_row.pack(fill=X, pady=(10, 0))
        Label(naming_row, text="Naming Mode", fg="#d9d4c7", bg="#161a1f").pack(side=LEFT)
        mode_box = ttk.Combobox(naming_row, textvariable=self.naming_mode, values=sorted(NAMING_MODES), width=14, state="readonly")
        mode_box.pack(side=LEFT, padx=8)
        Label(naming_row, text="Template", fg="#d9d4c7", bg="#161a1f").pack(side=LEFT, padx=(10, 4))
        Entry(naming_row, textvariable=self.name_template).pack(side=LEFT, fill=X, expand=True)
        Button(naming_row, text="Reset Template", command=lambda: self.name_template.set(DEFAULT_NAME_TEMPLATE)).pack(side=LEFT, padx=(8, 0))

        help_text = "Template fields: {title}, {source}, {site}, {playlist}, {playlist_index}, {date}, {kind}, {extractor}, {url_host}"
        Label(tab, text=help_text, fg="#9fb2aa", bg="#161a1f").pack(anchor="w", pady=(8, 0))

    def _build_profiles_tab(self, tab: Frame) -> None:
        """Build save/load profile controls for reusable global settings."""
        top_row = Frame(tab, bg="#161a1f")
        top_row.pack(fill=X)
        Label(top_row, text="Profile Name", fg="#d9d4c7", bg="#161a1f").pack(side=LEFT)
        Entry(top_row, textvariable=self.profile_name, width=28).pack(side=LEFT, padx=8)
        Button(top_row, text="Save Profile", command=self.save_current_profile).pack(side=LEFT, padx=4)
        Button(top_row, text="Load Profile", command=self.load_selected_profile).pack(side=LEFT, padx=4)
        Button(top_row, text="Refresh", command=self.refresh_profiles).pack(side=LEFT, padx=4)

        list_row = Frame(tab, bg="#161a1f")
        list_row.pack(fill=X, pady=(8, 0))
        self.profile_list = Listbox(list_row, height=4)
        self.profile_list.pack(side=LEFT, fill=X, expand=True)
        profile_scroll = Scrollbar(list_row, orient="vertical", command=self.profile_list.yview)
        profile_scroll.pack(side=RIGHT, fill=Y)
        self.profile_list.configure(yscrollcommand=profile_scroll.set)

    def add_single_url(self) -> None:
        """Add the single URL entry field to the processing queue."""
        value = self.single_url.get().strip()
        if not value:
            return
        self.queue_list.insert(END, value)
        self.single_url.set("")
        self.log(f"Queued: {value}\n")
        self.update_status()

    def load_batch(self) -> None:
        """Load a URL batch file into the GUI queue."""
        path = filedialog.askopenfilename(
            title="Choose URL batch file",
            filetypes=[("Text files", "*.txt *.list *.urls"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            items = parse_batch_file(Path(path))
        except OSError as exc:
            txt_path, md_path = write_crash_report("gui_batch_load_error", exc, extra={"path": path})
            messagebox.showerror("Video2WAV", friendly_exception(exc))
            self.log(f"Crash report written:\n  {txt_path}\n  {md_path}\n")
            return
        for item in items:
            text = item.url if not item.custom_label else f"{item.url} | {item.custom_label}"
            self.queue_list.insert(END, text)
        self.log(f"Loaded {len(items)} item(s) from {path}\n")
        self.update_status()

    def browse_output(self) -> None:
        """Open a folder picker for the output root."""
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_root.set(path)

    def reset_output_default(self) -> None:
        """Reset the final WAV directory to the project downloads folder."""
        self.output_root.set(str(default_output_root(self.script_dir)))
        self.log(f"Output directory reset to: {self.output_root.get()}\n")

    def current_output_settings(self) -> OutputSettings:
        """Return normalized output settings from the current GUI controls."""
        settings = OutputSettings(
            output_root=self.output_root.get(),
            generate_json=self.generate_json.get(),
            organize_by_site=self.organize_by_site.get(),
            organize_by_date=self.organize_by_date.get(),
            organize_by_playlist=self.organize_by_playlist.get(),
            naming_mode=self.naming_mode.get(),
            name_template=self.name_template.get(),
            profile_name=self.profile_name.get(),
        ).normalized(default_output_root(self.script_dir))
        self.output_root.set(settings.output_root)
        self.naming_mode.set(settings.naming_mode)
        self.name_template.set(settings.name_template)
        self.profile_name.set(settings.profile_name)
        return settings

    def apply_output_settings(self, settings: OutputSettings) -> None:
        """Apply loaded profile settings to the GUI controls."""
        normalized = settings.normalized(default_output_root(self.script_dir))
        self.output_root.set(normalized.output_root)
        self.generate_json.set(normalized.generate_json)
        self.organize_by_site.set(normalized.organize_by_site)
        self.organize_by_date.set(normalized.organize_by_date)
        self.organize_by_playlist.set(normalized.organize_by_playlist)
        self.naming_mode.set(normalized.naming_mode)
        self.name_template.set(normalized.name_template)
        self.profile_name.set(normalized.profile_name)
        self.update_status()

    def refresh_profiles(self) -> None:
        """Refresh the saved-profile listbox."""
        if not hasattr(self, "profile_list"):
            return
        self.profile_list.delete(0, END)
        for name in list_profiles(self.script_dir):
            self.profile_list.insert(END, name)

    def save_current_profile(self) -> None:
        """Save current GUI settings into a reusable JSON profile."""
        try:
            path = save_profile(self.script_dir, self.current_output_settings())
            self.refresh_profiles()
            self.log(f"Saved profile: {path}\n")
        except Exception as exc:
            txt_path, md_path = write_crash_report("gui_profile_save_error", exc, extra={"profile": self.profile_name.get()})
            messagebox.showerror("Video2WAV", f"Could not save profile:\n{friendly_exception(exc)}")
            self.log(f"Profile save failed. Crash report written:\n  {txt_path}\n  {md_path}\n")

    def selected_profile_name(self) -> str:
        """Return the selected profile list item or the typed profile name."""
        selection = self.profile_list.curselection() if hasattr(self, "profile_list") else ()
        if selection:
            return str(self.profile_list.get(selection[0]))
        return self.profile_name.get().strip() or "default"

    def load_selected_profile(self) -> None:
        """Load the selected or typed profile into the GUI controls."""
        self.load_profile_by_name(self.selected_profile_name())

    def load_profile_by_name(self, name: str) -> None:
        """Load a named profile into the GUI controls."""
        try:
            settings = load_profile(self.script_dir, name)
            self.apply_output_settings(settings)
            self.log(f"Loaded profile: {name}\n")
        except Exception as exc:
            txt_path, md_path = write_crash_report("gui_profile_load_error", exc, extra={"profile": name})
            messagebox.showerror("Video2WAV", f"Could not load profile '{name}':\n{friendly_exception(exc)}")
            self.log(f"Profile load failed. Crash report written:\n  {txt_path}\n  {md_path}\n")

    def open_output_folder(self) -> None:
        """Open the configured output folder in Windows Explorer."""
        self.open_path(Path(self.output_root.get()).expanduser().resolve(), "output folder")

    def open_crashlogs(self) -> None:
        """Open the crashlogs folder in Windows Explorer."""
        self.open_path(self.script_dir / "crashlogs", "crashlogs folder")

    def open_last_output(self) -> None:
        """Open the most recently produced WAV file."""
        if not self.last_output_path or not self.last_output_path.exists():
            self.log("No completed WAV output is available yet.\n")
            return
        self.open_file(self.last_output_path, "last WAV output")

    def copy_last_output_path(self) -> None:
        """Copy the most recently produced WAV path to the clipboard."""
        if not self.last_output_path:
            self.log("No completed WAV output path is available yet.\n")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(str(self.last_output_path))
            self.log(f"Copied last WAV path: {self.last_output_path}\n")
        except Exception as exc:
            txt_path, md_path = write_crash_report("gui_copy_last_output_path_error", exc, extra={"path": str(self.last_output_path)})
            self.log(f"Could not copy last WAV path: {friendly_exception(exc)}\nCrash report written:\n  {txt_path}\n  {md_path}\n")

    def open_path(self, path: Path, label: str) -> None:
        """Open a local folder and crash-log launcher failures."""
        try:
            ensure_dir(path)
            os.startfile(str(path))  # type: ignore[attr-defined]
            self.log(f"Opened {label}: {path}\n")
        except Exception as exc:
            txt_path, md_path = write_crash_report(f"gui_open_{label.replace(' ', '_')}_error", exc, extra={"path": str(path)})
            self.log(f"Could not open {label}: {friendly_exception(exc)}\nCrash report written:\n  {txt_path}\n  {md_path}\n")

    def open_file(self, path: Path, label: str) -> None:
        """Open a local file and crash-log launcher failures."""
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            self.log(f"Opened {label}: {path}\n")
        except Exception as exc:
            txt_path, md_path = write_crash_report(f"gui_open_{label.replace(' ', '_')}_error", exc, extra={"path": str(path)})
            self.log(f"Could not open {label}: {friendly_exception(exc)}\nCrash report written:\n  {txt_path}\n  {md_path}\n")

    def remove_selected(self) -> None:
        """Remove selected queue rows."""
        for index in reversed(self.queue_list.curselection()):
            self.queue_list.delete(index)
        self.update_status()

    def clear_queue(self) -> None:
        """Clear all queued URL rows."""
        self.queue_list.delete(0, END)
        self.log("Queue cleared.\n")
        self.update_status()

    def clear_terminal(self) -> None:
        """Clear the terminal/log panel without touching the processing queue."""
        self.terminal.configure(state=NORMAL)
        self.terminal.delete("1.0", END)
        self.terminal.configure(state=DISABLED)
        self.log("Terminal cleared.\n")

    def toggle_terminal(self) -> None:
        """Show or hide the bottom terminal/log panel."""
        if self.terminal_visible.get():
            self._animate_terminal(target_height=1, hide_at_end=True)
        else:
            self.terminal_frame.pack(side=BOTTOM, fill=BOTH)
            self.terminal_visible.set(True)
            self._animate_terminal(target_height=11, hide_at_end=False)

    def _animate_terminal(self, target_height: int, hide_at_end: bool) -> None:
        """Animate the terminal panel by stepping its text-widget height."""
        current = int(self.terminal.cget("height"))
        if current == target_height:
            if hide_at_end:
                self.terminal_frame.pack_forget()
                self.terminal_visible.set(False)
            return
        step = 1 if target_height > current else -1
        self.terminal.configure(height=current + step)
        self.root.after(18, lambda: self._animate_terminal(target_height, hide_at_end))

    def run_terminal_command(self) -> None:
        """Execute a small built-in command from the GUI terminal input."""
        raw = self.command_text.get().strip()
        self.command_text.set("")
        if not raw:
            return
        self.log(f"> {raw}\n")
        try:
            command, _, arg = raw.partition(" ")
            lowered = command.lower()
            if lowered == "add" and arg.strip():
                self.queue_list.insert(END, arg.strip())
                self.log(f"Queued: {arg.strip()}\n")
            elif lowered == "batch" and arg.strip():
                path = Path(arg.strip().strip('"'))
                if not path.exists():
                    self.log("Batch file not found.\n")
                    return
                for item in parse_batch_file(path):
                    text = item.url if not item.custom_label else f"{item.url} | {item.custom_label}"
                    self.queue_list.insert(END, text)
                self.log(f"Loaded batch: {path}\n")
            elif lowered == "start":
                self.start_processing()
            elif lowered == "clear":
                self.clear_queue()
            elif lowered in {"clear-log", "clear-terminal"}:
                self.clear_terminal()
            elif lowered == "output" and arg.strip():
                self.output_root.set(str(Path(arg.strip().strip('"')).expanduser().resolve()))
                self.log(f"Output directory set to: {self.output_root.get()}\n")
            elif lowered == "output-default":
                self.reset_output_default()
            elif lowered == "json" and arg.strip().lower() in {"on", "off"}:
                self.generate_json.set(arg.strip().lower() == "on")
                self.log(f"JSON metadata generation: {'on' if self.generate_json.get() else 'off'}\n")
            elif lowered == "naming" and arg.strip().lower() in NAMING_MODES:
                self.naming_mode.set(arg.strip().lower())
                self.log(f"Naming mode set to: {self.naming_mode.get()}\n")
            elif lowered == "template" and arg.strip():
                self.name_template.set(arg.strip())
                self.log(f"Name template set to: {self.name_template.get()}\n")
            elif lowered == "organize" and arg.strip():
                self.run_organize_command(arg.strip())
            elif lowered == "profile-save":
                if arg.strip():
                    self.profile_name.set(arg.strip())
                self.save_current_profile()
            elif lowered == "profile-load" and arg.strip():
                self.load_profile_by_name(arg.strip())
            elif lowered == "profiles":
                self.refresh_profiles()
                names = list_profiles(self.script_dir)
                self.log("Profiles: " + (", ".join(names) if names else "(none)") + "\n")
            elif lowered == "open-output":
                self.open_output_folder()
            elif lowered == "open-last":
                self.open_last_output()
            elif lowered == "copy-last":
                self.copy_last_output_path()
            elif lowered == "open-crashlogs":
                self.open_crashlogs()
            elif lowered == "status":
                self.update_status()
                self.log(f"{self.status_text.get()}\n")
            elif lowered == "hide":
                self.toggle_terminal()
            elif lowered == "help":
                self.log("Commands: add <url>, batch <path>, start, clear, clear-log, output <path>, output-default, json on/off, naming smart/template/ask/numbered, template <pattern>, organize site/date/playlist on/off, profile-save <name>, profile-load <name>, profiles, open-output, open-last, copy-last, open-crashlogs, status, hide, help\n")
            else:
                self.log("Unknown command. Type: help\n")
        except Exception as exc:
            txt_path, md_path = write_crash_report("gui_terminal_command_error", exc, extra={"command": raw})
            self.log(f"Terminal command failed: {friendly_exception(exc)}\nCrash report written:\n  {txt_path}\n  {md_path}\n")

    def run_organize_command(self, arg: str) -> None:
        """Apply terminal organization commands such as ``site off``."""
        parts = arg.split()
        if len(parts) != 2 or parts[0].lower() not in {"site", "date", "playlist"} or parts[1].lower() not in {"on", "off"}:
            self.log("Usage: organize site/date/playlist on/off\n")
            return
        enabled = parts[1].lower() == "on"
        target = parts[0].lower()
        if target == "site":
            self.organize_by_site.set(enabled)
        elif target == "date":
            self.organize_by_date.set(enabled)
        else:
            self.organize_by_playlist.set(enabled)
        self.log(f"Organization {target}: {'on' if enabled else 'off'}\n")

    def start_processing(self) -> None:
        """Validate current settings and start a background processing thread."""
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Video2WAV", "Processing is already running.")
            return
        items = self._queue_items()
        if not items:
            messagebox.showerror("Video2WAV", "Add at least one URL first.")
            return
        try:
            check_dependencies()
        except DownloadError as exc:
            txt_path, md_path = write_crash_report("gui_dependency_check_error", exc)
            messagebox.showerror("Dependency check failed", friendly_exception(exc))
            self.log(f"Dependency check failed: {friendly_exception(exc)}\nCrash report written:\n  {txt_path}\n  {md_path}\n")
            return

        settings = {
            "output_settings": self.current_output_settings(),
            "cookie_browser": self.cookie_browser.get().strip() or None,
            "verbose": self.verbose.get(),
            "auto_keep_both": self.auto_keep_both.get(),
            "static_queue": self.static_queue.get(),
        }
        self.update_status("Processing")
        self.worker = threading.Thread(target=self._process_items, args=(items, settings), daemon=True)
        self.worker.start()

    def _queue_items(self) -> List[GuiQueueItem]:
        """Convert GUI listbox rows into normalized queue items."""
        items: List[GuiQueueItem] = []
        for raw in self.queue_list.get(0, END):
            text = str(raw).strip()
            if not text:
                continue
            if "|" in text:
                url, label = [part.strip() for part in text.split("|", 1)]
                input_item = InputItem(url=url, custom_label=label or None)
            else:
                input_item = InputItem(url=text)
            items.append(GuiQueueItem(input_item=input_item, row_text=text))
        return items

    def _process_items(self, items: List[GuiQueueItem], settings: dict) -> None:
        """Process queued items on a worker thread with stdout redirected."""
        writer = QueueWriter(self.log_queue)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            for entry in items:
                try:
                    processed = self._process_one(entry.input_item, settings)
                    if processed and not settings["static_queue"]:
                        self.call_on_ui(self.remove_processed_row, entry.row_text)
                except Exception as exc:
                    txt_path, md_path = write_crash_report(
                        "gui_worker_item_error",
                        exc,
                        extra={"url": entry.input_item.url},
                    )
                    print(f"Unexpected GUI worker error: {friendly_exception(exc)}")
                    print(f"Crash report written:\n  {txt_path}\n  {md_path}")
                self.call_on_ui(self.update_status, "Processing")
            print("\nAll queued inputs have been processed.")
            self.call_on_ui(self.update_status, "Idle")

    def _process_one(self, item: InputItem, settings: dict) -> bool:
        """Process one queue item using GUI dialogs for choices and duplicates."""
        processed_any = False
        print(f"\nProcessing: {item.url}")
        try:
            safe_url = validate_user_url(item.url)
        except UrlSafetyError as exc:
            print(f"Rejected URL: {exc}")
            write_crash_report("gui_url_safety_error", exc, extra={"url": item.url})
            return False

        cookie_browser = settings["cookie_browser"]
        try:
            candidates = discover_candidates(safe_url, cookies_browser=cookie_browser, verbose=settings["verbose"])
        except DiscoveryError as exc:
            print(f"Could not inspect URL: {friendly_exception(exc)}")
            write_crash_report(
                "gui_discovery_error",
                exc,
                extra={"url": safe_url, "cookies_browser": cookie_browser or "(none)"},
            )
            if cookie_browser:
                return False
            retry = self.ask_cookie_retry(exc)
            if not retry:
                return False
            cookie_browser = retry
            candidates = discover_candidates(safe_url, cookies_browser=cookie_browser, verbose=settings["verbose"])

        selected = self.ask_candidate_selection(candidates)
        if not selected:
            print("No candidates selected.")
            return False

        output_settings: OutputSettings = settings["output_settings"]
        output_root = Path(output_settings.output_root).expanduser().resolve()
        for candidate in selected:
            target_dir = build_target_dir(output_root, candidate, output_settings)
            title_hint = item.custom_label or candidate.title
            custom_name = self.ask_output_name(candidate, title_hint) if output_settings.naming_mode == NAMING_ASK else None
            output_stub, used_title = build_output_stub(
                output_dir=target_dir,
                candidate=candidate,
                input_url=safe_url,
                settings=output_settings,
                custom_name=custom_name,
                fallback_title=title_hint,
            )

            duplicate_action = "keep_both" if settings["auto_keep_both"] else self.ask_duplicate_action(output_stub.with_suffix(".wav"))
            if duplicate_action == "skip":
                print(f"Skipping existing file: {output_stub.with_suffix('.wav').name}")
                continue

            try:
                result = download_candidate_to_wav(
                    candidate=candidate,
                    output_stub=output_stub,
                    overwrite=(duplicate_action == "overwrite"),
                    cookies_browser=cookie_browser,
                    verbose=settings["verbose"],
                )
            except DownloadError as exc:
                print(f"Download/conversion failed: {friendly_exception(exc)}")
                write_crash_report(
                    "gui_download_error",
                    exc,
                    extra={
                        "input_url": safe_url,
                        "candidate_url": candidate.url,
                        "candidate_title": candidate.title,
                        "output_stub": str(output_stub),
                    },
                )
                if cookie_browser:
                    continue
                retry = self.ask_cookie_retry(exc)
                if not retry:
                    continue
                result = download_candidate_to_wav(
                    candidate=candidate,
                    output_stub=output_stub,
                    overwrite=(duplicate_action == "overwrite"),
                    cookies_browser=retry,
                    verbose=settings["verbose"],
                )

            sidecar = None
            if output_settings.generate_json:
                sidecar = write_metadata_sidecar(
                    result.final_path,
                    build_metadata(safe_url, candidate, result, used_title, output_settings),
                )
            print("\nDone.")
            print(f"Saved: {result.final_path}")
            if sidecar:
                print(f"Metadata: {sidecar}")
            else:
                print("Metadata: disabled")
            self.last_output_path = result.final_path
            processed_any = True
        return processed_any

    def remove_processed_row(self, row_text: str) -> None:
        """Remove the first matching processed row from the listbox.

        This is used only when Static Queue is disabled. It intentionally
        removes a single matching row so duplicate URLs remain predictable.
        """
        for idx, value in enumerate(self.queue_list.get(0, END)):
            if str(value).strip() == row_text:
                self.queue_list.delete(idx)
                self.log(f"Removed processed queue item: {row_text}\n")
                break
        self.update_status()

    def update_status(self, state: Optional[str] = None) -> None:
        """Refresh queue count and processing state shown above the listbox."""
        if state is not None:
            self.processing_state = state
        count = self.queue_list.size()
        mode = "Static Queue" if self.static_queue.get() else "Auto-clear"
        self.status_text.set(f"Queue: {count} | {self.processing_state} | {mode}")

    def ask_candidate_selection(self, candidates: Sequence[MediaCandidate]) -> List[MediaCandidate]:
        """Ask the user which discovered candidates should be converted."""
        if len(candidates) == 1:
            print(f"Selected: {candidates[0].title or candidates[0].url}")
            return [candidates[0]]

        def dialog() -> List[MediaCandidate]:
            """Perform the dialog step for the Video2WAV workflow."""
            win = Toplevel(self.root)
            win.title("Choose Video2WAV Candidates")
            win.geometry("900x440")
            win.transient(self.root)
            win.grab_set()
            Label(win, text="Select one, several, a range, or all. Examples: 1-3, 1,3,5-6, all").pack(anchor="w", padx=10, pady=8)
            listbox = Listbox(win, selectmode="extended", height=14)
            listbox.pack(fill=BOTH, expand=True, padx=10)
            for idx, candidate in enumerate(candidates, start=1):
                listbox.insert(END, _candidate_label(idx, candidate))
            entry_var = StringVar(value="all")
            row = Frame(win)
            row.pack(fill=X, padx=10, pady=8)
            Label(row, text="Selection").pack(side=LEFT)
            Entry(row, textvariable=entry_var).pack(side=LEFT, fill=X, expand=True, padx=8)
            result: List[MediaCandidate] = []

            def choose() -> None:
                """Perform the choose step for the Video2WAV workflow."""
                raw = entry_var.get().strip()
                if not raw and listbox.curselection():
                    indexes = [i + 1 for i in listbox.curselection()]
                else:
                    try:
                        indexes = parse_selection(raw, len(candidates))
                    except ValueError:
                        indexes = []
                if not indexes:
                    messagebox.showerror("Video2WAV", "Invalid selection.", parent=win)
                    return
                result.extend(candidates[i - 1] for i in indexes)
                win.destroy()

            Button(row, text="Process Selection", command=choose).pack(side=RIGHT)
            Button(row, text="Cancel", command=win.destroy).pack(side=RIGHT, padx=8)
            self.root.wait_window(win)
            return result

        return self.call_on_ui(dialog)

    def ask_duplicate_action(self, existing_path: Path) -> str:
        """Ask how to handle a duplicate WAV path from the GUI."""
        if not existing_path.exists():
            return "overwrite"

        def dialog() -> str:
            """Perform the dialog step for the Video2WAV workflow."""
            answer = messagebox.askyesnocancel(
                "Duplicate WAV",
                f"A WAV file already exists:\n{existing_path.name}\n\nYes = overwrite\nNo = keep both\nCancel = skip",
                parent=self.root,
            )
            if answer is True:
                return "overwrite"
            if answer is False:
                return "keep_both"
            return "skip"

        return self.call_on_ui(dialog)

    def ask_output_name(self, candidate: MediaCandidate, fallback_title: str) -> Optional[str]:
        """Ask the user for a final WAV filename stem when Ask mode is enabled."""
        def dialog() -> Optional[str]:
            """Show the filename prompt on the Tkinter main thread."""
            default_name = fallback_title or candidate.title or candidate.source_name or "audio"
            return simpledialog.askstring(
                "Final WAV Name",
                "Enter the final WAV filename without extension.\nLeave blank to use the smart generated name.",
                initialvalue=default_name,
                parent=self.root,
            )

        value = self.call_on_ui(dialog)
        return value.strip() if value else None

    def ask_cookie_retry(self, exc: BaseException) -> Optional[str]:
        """Ask whether extraction should retry with browser cookies."""
        def dialog() -> Optional[str]:
            """Perform the dialog step for the Video2WAV workflow."""
            ok = messagebox.askyesno(
                "Cookie Retry",
                "Extraction failed and may require browser cookies.\n\n"
                "Cookie import can expose login/session access to yt-dlp. Use it only for trusted sites and prefer a dedicated browser profile.\n\n"
                f"Reason: {friendly_exception(exc)}\n\nRetry with browser cookies?",
                parent=self.root,
            )
            if not ok:
                return None
            return simpledialog.askstring("Cookie Browser", "Browser name (chrome, edge, firefox, brave):", parent=self.root)

        value = self.call_on_ui(dialog)
        return value.strip().lower() if value else None

    def call_on_ui(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Run a callable on Tkinter's main thread and return its result."""
        result_queue: "queue.Queue[T]" = queue.Queue(maxsize=1)
        self.ui_queue.put((func, args, kwargs, result_queue))
        return result_queue.get()

    def _poll_ui_requests(self) -> None:
        """Service blocking UI requests made by worker threads."""
        while True:
            try:
                func, args, kwargs, result_queue = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            result_queue.put(func(*args, **kwargs))
        self.root.after(50, self._poll_ui_requests)

    def log(self, text: str) -> None:
        """Append text to the bottom terminal/log panel."""
        self.terminal.configure(state=NORMAL)
        self.terminal.insert(END, text)
        self.terminal.see(END)
        self.terminal.configure(state=DISABLED)

    def _poll_logs(self) -> None:
        """Move pending worker-thread log text into the terminal widget."""
        while True:
            try:
                text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log(text)
        self.root.after(80, self._poll_logs)


def launch_gui(script_dir: Path) -> int:
    """Create the Tk root window and run the GUI event loop."""
    root = Tk()
    Video2WAVGui(root=root, script_dir=script_dir)
    root.mainloop()
    return 0
