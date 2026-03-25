import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import TkinterDnD

from errors import ConfigError, ImageSetError
from filename_template import (
    DEFAULT_LEGACY_INPUT,
    DEFAULT_LEGACY_OUTPUT,
    DEFAULT_STEREO_INPUT,
    DEFAULT_STEREO_OUTPUT_OVER_UNDER,
    DEFAULT_STEREO_OUTPUT_SEPARATE,
    INPUT_KEYWORDS_LEGACY,
    INPUT_KEYWORDS_STEREO,
    OUTPUT_KEYWORDS_LEGACY,
    OUTPUT_KEYWORDS_STEREO_SEPARATE,
    input_template_uses_render_pass,
)
from naming_ui import NamingSchemeEntry
from nDisplayMerger import (
    legacy_numeric_frame_span_strings,
    list_legacy_frame_keys,
    list_legacy_render_passes,
    main as run_legacy_merger,
)
from stereo_merger import (
    StereoOutputMode,
    coerce_stereo_output_mode,
    list_paired_stereo_frames,
    list_stereo_render_passes,
    main as run_stereo_merger,
    resolve_stereo_output_dir,
    stereo_numeric_frame_span_strings,
)

_STEREO_MODE_LABEL_TO_VALUE = {
    "Equirectangular stereo (over/under)": StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER.value,
    "Equirectangular mono": StereoOutputMode.EQUIRECTANGULAR_MONO_SEPARATE_EYES.value,
}
_STEREO_MODE_VALUE_TO_LABEL = {v: k for k, v in _STEREO_MODE_LABEL_TO_VALUE.items()}

_SETTINGS_BASE = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
SETTINGS_PATH = os.path.join(_SETTINGS_BASE, "settings.json")
_APP_ICON_PATH = os.path.join(_SETTINGS_BASE, "assets", "app.ico")
_RANGE_REFRESH_MS = 400


def _apply_window_icon(root: tk.Tk) -> None:
    """Title-bar icon: dev uses assets/app.ico; frozen app uses icon embedded in the .exe (PyInstaller --icon)."""
    try:
        if getattr(sys, "frozen", False):
            # onefile exe has no loose assets/ folder next to the binary unless added with --add-data
            root.iconbitmap(default=os.path.abspath(sys.executable))
        elif os.path.isfile(_APP_ICON_PATH):
            root.iconbitmap(_APP_ICON_PATH)
    except tk.TclError:
        pass
_MAX_UI_WORKERS = 32

HELP_LEGACY = (
    "Stitches your rendered viewport images into one picture per frame, using the layout from the "
    "nDisplay config you choose.\n\n"
    "Only PNG and JPEG inputs are supported.\n\n"
    "Every placeholder used in output naming must also appear in input naming (so the app knows how "
    "to read each part of the filenames).\n\n"
    "If you use render passes: when more than one pass is exported, include {render_pass} in the "
    "output template as well, or files from different passes would overwrite each other.\n\n"
)

HELP_STEREO = (
    "Turns cubemap face renders (six directions per eye) into a 360° equirectangular image. "
    "You can save a single over/under stereo file or separate files per eye, depending on output mode.\n\n"
    "Only PNG and JPEG inputs are supported.\n\n"
    "In each filename, the camera name part must include exactly one face word on its own: "
    "BACK, LEFT, FRONT, RIGHT, UP, or DOWN (any case).\n\n"
    "In Equirectangular mono mode, output naming must include {eye} so left and right files do not collide.\n\n"
    "If you use render passes and export more than one pass, add {render_pass} to output naming too.\n\n"
    "Large cubemap images use a lot of memory; try one or two workers if the app struggles."
)


def _format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"

    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {sec}s"


def load_settings():
    if not os.path.isfile(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_settings(values):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
    except OSError:
        pass


def save_partial_settings(updates):
    """Merge updates into existing settings file."""
    cur = load_settings()
    cur.update(updates)
    save_settings(cur)


def _norm_drop_path(data):
    path = os.path.normpath(data.strip("{}"))
    return os.path.abspath(path)


def _parse_workers_ui(raw):
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    if n < 1 or n > _MAX_UI_WORKERS:
        return None
    return n


def build_ui(root):
    cancel_event = threading.Event()
    pause_event = threading.Event()

    settings = load_settings()

    legacy_input_dir = tk.StringVar(value=settings.get("legacy_input_dir", ""))
    legacy_ndisplay = tk.StringVar(value=settings.get("legacy_ndisplay", ""))
    legacy_output_dir = tk.StringVar(value=settings.get("legacy_output_dir", ""))
    legacy_frame_start = tk.StringVar(value=settings.get("legacy_frame_start", ""))
    legacy_frame_end = tk.StringVar(value=settings.get("legacy_frame_end", ""))

    stereo_left_dir = tk.StringVar(value=settings.get("stereo_left_dir", ""))
    stereo_right_dir = tk.StringVar(value=settings.get("stereo_right_dir", ""))
    stereo_output_dir = tk.StringVar(value=settings.get("stereo_output_dir", ""))
    stereo_frame_start = tk.StringVar(value=settings.get("stereo_frame_start", ""))
    stereo_frame_end = tk.StringVar(value=settings.get("stereo_frame_end", ""))

    legacy_max_workers = tk.StringVar(
        value=str(settings.get("legacy_max_workers", 4))
    )
    stereo_max_workers = tk.StringVar(
        value=str(settings.get("stereo_max_workers", 2))
    )
    _saved_stereo_mode = coerce_stereo_output_mode(
        settings.get("stereo_output_mode")
    ).value
    stereo_mode_var = tk.StringVar(
        value=_STEREO_MODE_VALUE_TO_LABEL.get(
            _saved_stereo_mode,
            "Equirectangular stereo (over/under)",
        )
    )

    legacy_input_naming = tk.StringVar(
        value=settings.get("legacy_input_naming", DEFAULT_LEGACY_INPUT)
    )
    legacy_output_naming = tk.StringVar(
        value=settings.get("legacy_output_naming", DEFAULT_LEGACY_OUTPUT)
    )
    stereo_input_naming = tk.StringVar(
        value=settings.get("stereo_input_naming", DEFAULT_STEREO_INPUT)
    )
    stereo_out_ou = tk.StringVar(
        value=settings.get(
            "stereo_output_naming_ou", DEFAULT_STEREO_OUTPUT_OVER_UNDER
        )
    )
    stereo_out_sep = tk.StringVar(
        value=settings.get(
            "stereo_output_naming_sep", DEFAULT_STEREO_OUTPUT_SEPARATE
        )
    )
    stereo_output_naming = tk.StringVar()
    if stereo_mode_var.get() == "Equirectangular mono":
        stereo_output_naming.set(stereo_out_sep.get())
    else:
        stereo_output_naming.set(stereo_out_ou.get())
    _prev_stereo_mode = [stereo_mode_var.get()]

    legacy_rp_vars = {}
    stereo_rp_vars = {}

    worker_running = [False]

    def persist_settings():
        # Merge into existing JSON so naming keys saved on Run are not erased on window close.
        _persist_stereo_output_field_into_active_bucket()
        cur = load_settings()
        cur.update(
            {
                "legacy_input_dir": legacy_input_dir.get(),
                "legacy_ndisplay": legacy_ndisplay.get(),
                "legacy_output_dir": legacy_output_dir.get(),
                "legacy_frame_start": legacy_frame_start.get(),
                "legacy_frame_end": legacy_frame_end.get(),
                "stereo_left_dir": stereo_left_dir.get(),
                "stereo_right_dir": stereo_right_dir.get(),
                "stereo_output_dir": stereo_output_dir.get(),
                "stereo_frame_start": stereo_frame_start.get(),
                "stereo_frame_end": stereo_frame_end.get(),
                "stereo_output_mode": _STEREO_MODE_LABEL_TO_VALUE.get(
                    stereo_mode_var.get(),
                    StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER.value,
                ),
                "legacy_max_workers": legacy_max_workers.get(),
                "stereo_max_workers": stereo_max_workers.get(),
                "legacy_render_pass_checked": {
                    k: bool(v.get()) for k, v in legacy_rp_vars.items()
                },
                "stereo_render_pass_checked": {
                    k: bool(v.get()) for k, v in stereo_rp_vars.items()
                },
            }
        )
        save_settings(cur)

    def _persist_stereo_output_field_into_active_bucket():
        if stereo_mode_var.get() == "Equirectangular mono":
            stereo_out_sep.set(stereo_output_naming.get())
        else:
            stereo_out_ou.set(stereo_output_naming.get())

    def _on_stereo_mode_change(*_):
        prev = _prev_stereo_mode[0]
        cur = stereo_mode_var.get()
        if prev != cur:
            if prev == "Equirectangular mono":
                stereo_out_sep.set(stereo_output_naming.get())
            else:
                stereo_out_ou.set(stereo_output_naming.get())
            if cur == "Equirectangular mono":
                stereo_output_naming.set(stereo_out_sep.get())
            else:
                stereo_output_naming.set(stereo_out_ou.get())
        _prev_stereo_mode[0] = cur

    main_frame = ttk.Frame(root, padding=(12, 12, 12, 12))
    main_frame.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    notebook = ttk.Notebook(main_frame)
    notebook.grid(row=0, column=0, sticky="nsew")
    main_frame.rowconfigure(0, weight=1)
    main_frame.columnconfigure(0, weight=1)

    tab_legacy = ttk.Frame(notebook, padding=(0, 8, 0, 0))
    tab_stereo = ttk.Frame(notebook, padding=(0, 8, 0, 0))
    notebook.add(tab_legacy, text="Config Merger")
    notebook.add(tab_stereo, text="Stereo VR Merger")

    for tab in (tab_legacy, tab_stereo):
        tab.columnconfigure(0, weight=0)
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=0)

    footer = ttk.Frame(main_frame)
    footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
    footer.columnconfigure(2, weight=1)

    stop_btn = ttk.Button(footer, text="Stop", state=tk.DISABLED)
    stop_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))

    run_pause_resume_btn = ttk.Button(footer, text="Run", width=10)
    run_pause_resume_btn.grid(row=0, column=1, sticky="w", padx=(0, 8))

    progressbar = ttk.Progressbar(footer, orient=tk.HORIZONTAL, length=300, mode="determinate")
    progressbar.grid(row=0, column=2, sticky="ew")

    frame_status_label = tk.Label(footer, text="")
    frame_status_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

    progress_label = tk.Label(footer, text="")
    progress_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def set_buttons_idle():
        worker_running[0] = False
        stop_btn.config(state=tk.DISABLED)
        run_pause_resume_btn.config(text="Run", state=tk.NORMAL)

    def set_buttons_running():
        worker_running[0] = True
        stop_btn.config(state=tk.NORMAL)
        run_pause_resume_btn.config(text="Pause", state=tk.NORMAL)

    def set_buttons_paused():
        stop_btn.config(state=tk.NORMAL)
        run_pause_resume_btn.config(text="Resume", state=tk.NORMAL)

    def _update_progressbar_ui(value, max_value, start_time):
        progressbar["value"] = value
        progressbar["maximum"] = max_value

        if value <= 0 or max_value <= 0:
            progress_label.config(text="")
            return

        elapsed_time = time.time() - start_time
        if value >= max_value:
            progress_label.config(text=f"Completed in {_format_duration(elapsed_time)}")
            return

        remaining_time = (max_value - value) * (elapsed_time / value)
        progress_label.config(text=f"Time remaining: {_format_duration(remaining_time)}")

    def update_progressbar(value, max_value, start_time):
        try:
            root.after(0, _update_progressbar_ui, value, max_value, start_time)
        except RuntimeError:
            pass

    def update_frame_status(frame_key, current_index, total_in_range):
        """frame_key = sequence id from files; current_index/total = 1-based position in export batch."""
        def _do():
            frame_status_label.config(
                text=f"Merging frame {frame_key} ({current_index}/{total_in_range})"
            )

        try:
            root.after(0, _do)
        except RuntimeError:
            pass

    def reset_progress_ui():
        progressbar["value"] = 0
        progressbar["maximum"] = 0
        progress_label.config(text="")
        frame_status_label.config(text="")

    def on_stop():
        pause_event.clear()
        cancel_event.set()

    def on_pause():
        pause_event.set()
        set_buttons_paused()

    def on_resume():
        pause_event.clear()
        set_buttons_running()

    stop_btn.config(command=on_stop)

    # --- Legacy tab ---
    row_pad_y = 4
    row = 0

    def show_help_legacy():
        messagebox.showinfo("Standard Config Merger — Help", HELP_LEGACY)

    ttk.Button(tab_legacy, text="?", width=3, command=show_help_legacy).grid(
        row=row, column=2, sticky="e", pady=(0, 6)
    )
    row += 1

    ttk.Label(tab_legacy, text="Input Directory:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    legacy_in_entry = tk.Entry(tab_legacy, textvariable=legacy_input_dir, width=50)
    legacy_in_entry.grid(row=row, column=1, sticky="ew", pady=row_pad_y)
    legacy_in_entry.drop_target_register("DND_Files")
    legacy_in_entry.dnd_bind("<<Drop>>", lambda e: legacy_input_dir.set(_norm_drop_path(e.data)))

    def browse_legacy_in():
        path = filedialog.askdirectory()
        if path:
            legacy_input_dir.set(path)

    ttk.Button(tab_legacy, text="Browse", command=browse_legacy_in).grid(
        row=row, column=2, padx=(8, 0), pady=row_pad_y
    )
    row += 1

    ttk.Label(tab_legacy, text="nDisplay Config:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    legacy_cfg_entry = tk.Entry(tab_legacy, textvariable=legacy_ndisplay, width=50)
    legacy_cfg_entry.grid(row=row, column=1, sticky="ew", pady=row_pad_y)
    legacy_cfg_entry.drop_target_register("DND_Files")
    legacy_cfg_entry.dnd_bind("<<Drop>>", lambda e: legacy_ndisplay.set(_norm_drop_path(e.data)))

    def browse_legacy_cfg():
        path = filedialog.askopenfilename(filetypes=[("nDisplay Config Files", "*.ndisplay")])
        if path:
            legacy_ndisplay.set(path)

    ttk.Button(tab_legacy, text="Browse", command=browse_legacy_cfg).grid(
        row=row, column=2, padx=(8, 0), pady=row_pad_y
    )
    row += 1

    ttk.Label(tab_legacy, text="Input naming:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    NamingSchemeEntry(
        tab_legacy,
        legacy_input_naming,
        INPUT_KEYWORDS_LEGACY,
        width=50,
    ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=row_pad_y)
    row += 1

    ttk.Label(tab_legacy, text="Output naming:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    NamingSchemeEntry(
        tab_legacy,
        legacy_output_naming,
        OUTPUT_KEYWORDS_LEGACY,
        width=50,
    ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=row_pad_y)
    row += 1

    ttk.Label(tab_legacy, text="Output Directory (optional):").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    legacy_out_entry = tk.Entry(tab_legacy, textvariable=legacy_output_dir, width=50)
    legacy_out_entry.grid(row=row, column=1, sticky="ew", pady=row_pad_y)
    legacy_out_entry.drop_target_register("DND_Files")
    legacy_out_entry.dnd_bind("<<Drop>>", lambda e: legacy_output_dir.set(_norm_drop_path(e.data)))

    def browse_legacy_out():
        path = filedialog.askdirectory()
        if path:
            legacy_output_dir.set(path)

    ttk.Button(tab_legacy, text="Browse", command=browse_legacy_out).grid(
        row=row, column=2, padx=(8, 0), pady=row_pad_y
    )
    row += 1

    legacy_hint = tk.Label(
        tab_legacy,
        text="If left empty, output will be saved to a 'merged' folder inside the input directory.",
        fg="gray",
    )
    legacy_hint.grid(row=row, column=1, sticky="w", pady=(0, 6))
    row += 1

    def _on_legacy_out_change(*_):
        if legacy_output_dir.get().strip():
            legacy_hint.grid_remove()
        else:
            legacy_hint.grid()

    legacy_output_dir.trace_add("write", _on_legacy_out_change)
    _on_legacy_out_change()

    ttk.Label(tab_legacy, text="Start frame:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    tk.Entry(tab_legacy, textvariable=legacy_frame_start, width=50).grid(
        row=row, column=1, sticky="w", pady=row_pad_y
    )
    row += 1

    ttk.Label(tab_legacy, text="End frame:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    tk.Entry(tab_legacy, textvariable=legacy_frame_end, width=50).grid(
        row=row, column=1, sticky="w", pady=row_pad_y
    )
    row += 1

    legacy_rp_grid_kw = dict(row=row, column=0, columnspan=3, sticky="ew", pady=row_pad_y)
    legacy_rp_lf = ttk.LabelFrame(tab_legacy, text="Render passes")
    legacy_rp_inner = ttk.Frame(legacy_rp_lf)
    legacy_rp_inner.pack(fill="x", padx=4, pady=4)
    legacy_rp_lf.grid(**legacy_rp_grid_kw)
    legacy_rp_lf.grid_remove()
    row += 1

    ttk.Label(tab_legacy, text="Workers:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    ttk.Spinbox(
        tab_legacy,
        from_=1,
        to=_MAX_UI_WORKERS,
        textvariable=legacy_max_workers,
        width=6,
    ).grid(row=row, column=1, sticky="w", pady=row_pad_y)
    row += 1

    # --- Stereo tab ---
    row = 0

    def show_help_stereo():
        messagebox.showinfo("Stereo VR Merger — Help", HELP_STEREO)

    ttk.Button(tab_stereo, text="?", width=3, command=show_help_stereo).grid(
        row=row, column=2, sticky="e", pady=(0, 6)
    )
    row += 1

    ttk.Label(tab_stereo, text="Left Eye Directory:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    stereo_left_entry = tk.Entry(tab_stereo, textvariable=stereo_left_dir, width=50)
    stereo_left_entry.grid(row=row, column=1, sticky="ew", pady=row_pad_y)
    stereo_left_entry.drop_target_register("DND_Files")
    stereo_left_entry.dnd_bind("<<Drop>>", lambda e: stereo_left_dir.set(_norm_drop_path(e.data)))

    def browse_stereo_left():
        path = filedialog.askdirectory()
        if path:
            stereo_left_dir.set(path)

    ttk.Button(tab_stereo, text="Browse", command=browse_stereo_left).grid(
        row=row, column=2, padx=(8, 0), pady=row_pad_y
    )
    row += 1

    ttk.Label(tab_stereo, text="Right Eye Directory:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    stereo_right_entry = tk.Entry(tab_stereo, textvariable=stereo_right_dir, width=50)
    stereo_right_entry.grid(row=row, column=1, sticky="ew", pady=row_pad_y)
    stereo_right_entry.drop_target_register("DND_Files")
    stereo_right_entry.dnd_bind("<<Drop>>", lambda e: stereo_right_dir.set(_norm_drop_path(e.data)))

    def browse_stereo_right():
        path = filedialog.askdirectory()
        if path:
            stereo_right_dir.set(path)

    ttk.Button(tab_stereo, text="Browse", command=browse_stereo_right).grid(
        row=row, column=2, padx=(8, 0), pady=row_pad_y
    )
    row += 1

    ttk.Label(tab_stereo, text="Input naming:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    NamingSchemeEntry(
        tab_stereo,
        stereo_input_naming,
        INPUT_KEYWORDS_STEREO,
        width=50,
    ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=row_pad_y)
    row += 1

    ttk.Label(tab_stereo, text="Output mode:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    stereo_mode_combo = ttk.Combobox(
        tab_stereo,
        textvariable=stereo_mode_var,
        values=list(_STEREO_MODE_LABEL_TO_VALUE.keys()),
        state="readonly",
        width=47,
    )
    stereo_mode_combo.grid(row=row, column=1, columnspan=2, sticky="w", pady=row_pad_y)
    stereo_mode_var.trace_add("write", _on_stereo_mode_change)
    row += 1

    ttk.Label(tab_stereo, text="Output naming:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    NamingSchemeEntry(
        tab_stereo,
        stereo_output_naming,
        OUTPUT_KEYWORDS_STEREO_SEPARATE,
        width=50,
    ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=row_pad_y)
    row += 1

    ttk.Label(tab_stereo, text="Output Directory (optional):").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    stereo_out_entry = tk.Entry(tab_stereo, textvariable=stereo_output_dir, width=50)
    stereo_out_entry.grid(row=row, column=1, sticky="ew", pady=row_pad_y)
    stereo_out_entry.drop_target_register("DND_Files")
    stereo_out_entry.dnd_bind("<<Drop>>", lambda e: stereo_output_dir.set(_norm_drop_path(e.data)))

    def browse_stereo_out():
        path = filedialog.askdirectory()
        if path:
            stereo_output_dir.set(path)

    ttk.Button(tab_stereo, text="Browse", command=browse_stereo_out).grid(
        row=row, column=2, padx=(8, 0), pady=row_pad_y
    )
    row += 1

    stereo_hint = tk.Label(tab_stereo, text="", fg="gray", wraplength=520, justify="left")
    stereo_hint.grid(row=row, column=1, columnspan=2, sticky="w", pady=(0, 6))
    row += 1

    def _refresh_stereo_hint(*_):
        if stereo_output_dir.get().strip():
            stereo_hint.grid_remove()
            return
        stereo_hint.grid()
        if stereo_mode_var.get() == "Equirectangular mono":
            stereo_hint.config(
                text="If left empty, base output is 'merged_stereo' next to the left eye folder's parent; "
                "per-eye paths follow your output naming template (default uses left_eye/ and right_eye/)."
            )
        else:
            stereo_hint.config(
                text="If left empty, output goes to 'merged_stereo' next to the left eye folder's parent path."
            )

    stereo_output_dir.trace_add("write", _refresh_stereo_hint)
    stereo_mode_var.trace_add("write", _refresh_stereo_hint)
    _refresh_stereo_hint()

    ttk.Label(tab_stereo, text="Start frame:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    tk.Entry(tab_stereo, textvariable=stereo_frame_start, width=50).grid(
        row=row, column=1, sticky="w", pady=row_pad_y
    )
    row += 1

    ttk.Label(tab_stereo, text="End frame:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    tk.Entry(tab_stereo, textvariable=stereo_frame_end, width=50).grid(
        row=row, column=1, sticky="w", pady=row_pad_y
    )
    row += 1

    stereo_rp_grid_kw = dict(row=row, column=0, columnspan=3, sticky="ew", pady=row_pad_y)
    stereo_rp_lf = ttk.LabelFrame(tab_stereo, text="Render passes")
    stereo_rp_inner = ttk.Frame(stereo_rp_lf)
    stereo_rp_inner.pack(fill="x", padx=4, pady=4)
    stereo_rp_lf.grid(**stereo_rp_grid_kw)
    stereo_rp_lf.grid_remove()
    row += 1

    ttk.Label(tab_stereo, text="Workers:").grid(
        row=row, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    ttk.Spinbox(
        tab_stereo,
        from_=1,
        to=_MAX_UI_WORKERS,
        textvariable=stereo_max_workers,
        width=6,
    ).grid(row=row, column=1, sticky="w", pady=row_pad_y)
    row += 1

    # --- debounced range refresh & render-pass checkboxes ---
    legacy_refresh_after = [None]
    stereo_refresh_after = [None]

    def save_naming_and_render_passes_on_run():
        _persist_stereo_output_field_into_active_bucket()
        save_partial_settings(
            {
                "legacy_input_naming": legacy_input_naming.get(),
                "legacy_output_naming": legacy_output_naming.get(),
                "stereo_input_naming": stereo_input_naming.get(),
                "stereo_output_naming_ou": stereo_out_ou.get(),
                "stereo_output_naming_sep": stereo_out_sep.get(),
                "legacy_render_pass_checked": {
                    k: bool(v.get()) for k, v in legacy_rp_vars.items()
                },
                "stereo_render_pass_checked": {
                    k: bool(v.get()) for k, v in stereo_rp_vars.items()
                },
            }
        )

    def _rebuild_legacy_render_passes():
        prev = {k: bool(v.get()) for k, v in legacy_rp_vars.items()}
        legacy_rp_vars.clear()
        for w in legacy_rp_inner.winfo_children():
            w.destroy()
        tpl = (legacy_input_naming.get() or "").strip() or DEFAULT_LEGACY_INPUT
        if not input_template_uses_render_pass(tpl):
            legacy_rp_lf.grid_remove()
            return
        inp_d = legacy_input_dir.get().strip()
        cfg_d = legacy_ndisplay.get().strip()
        if not inp_d or not cfg_d or not os.path.isdir(inp_d) or not os.path.isfile(cfg_d):
            legacy_rp_lf.grid_remove()
            return
        try:
            passes = list_legacy_render_passes(inp_d, cfg_d, tpl)
        except (ConfigError, ImageSetError, OSError):
            legacy_rp_lf.grid_remove()
            return
        if not passes:
            legacy_rp_lf.grid_remove()
            return
        saved = load_settings().get("legacy_render_pass_checked") or {}
        for p in passes:
            v = tk.BooleanVar(value=prev.get(p, saved.get(p, True)))
            legacy_rp_vars[p] = v
            ttk.Checkbutton(legacy_rp_inner, text=p, variable=v).pack(anchor="w")
        legacy_rp_lf.grid(**legacy_rp_grid_kw)

    def _rebuild_stereo_render_passes():
        prev = {k: bool(v.get()) for k, v in stereo_rp_vars.items()}
        stereo_rp_vars.clear()
        for w in stereo_rp_inner.winfo_children():
            w.destroy()
        tpl = (stereo_input_naming.get() or "").strip() or DEFAULT_STEREO_INPUT
        if not input_template_uses_render_pass(tpl):
            stereo_rp_lf.grid_remove()
            return
        left_p = stereo_left_dir.get().strip()
        right_p = stereo_right_dir.get().strip()
        if not left_p or not right_p or not os.path.isdir(left_p) or not os.path.isdir(right_p):
            stereo_rp_lf.grid_remove()
            return
        try:
            passes = list_stereo_render_passes(left_p, right_p, tpl)
        except (ImageSetError, OSError):
            stereo_rp_lf.grid_remove()
            return
        if not passes:
            stereo_rp_lf.grid_remove()
            return
        saved = load_settings().get("stereo_render_pass_checked") or {}
        for p in passes:
            v = tk.BooleanVar(value=prev.get(p, saved.get(p, True)))
            stereo_rp_vars[p] = v
            ttk.Checkbutton(stereo_rp_inner, text=p, variable=v).pack(anchor="w")
        stereo_rp_lf.grid(**stereo_rp_grid_kw)

    def _try_refresh_legacy_range():
        legacy_refresh_after[0] = None
        inp = legacy_input_dir.get().strip()
        cfg = legacy_ndisplay.get().strip()
        if not inp or not cfg or not os.path.isdir(inp) or not os.path.isfile(cfg):
            _rebuild_legacy_render_passes()
            return
        try:
            keys = list_legacy_frame_keys(inp, cfg, legacy_input_naming.get().strip() or None)
            fs, fe = legacy_numeric_frame_span_strings(keys)
            legacy_frame_start.set(fs)
            legacy_frame_end.set(fe)
        except (ConfigError, ImageSetError, OSError):
            pass
        _rebuild_legacy_render_passes()

    def _schedule_legacy_range_refresh(*_):
        if legacy_refresh_after[0] is not None:
            try:
                root.after_cancel(legacy_refresh_after[0])
            except tk.TclError:
                pass
        legacy_refresh_after[0] = root.after(_RANGE_REFRESH_MS, _try_refresh_legacy_range)

    def _try_refresh_stereo_range():
        stereo_refresh_after[0] = None
        left_p = stereo_left_dir.get().strip()
        right_p = stereo_right_dir.get().strip()
        if not left_p or not right_p or not os.path.isdir(left_p) or not os.path.isdir(right_p):
            _rebuild_stereo_render_passes()
            return
        try:
            keys = list_paired_stereo_frames(
                left_p,
                right_p,
                stereo_input_naming.get().strip() or None,
            )
            fs, fe = stereo_numeric_frame_span_strings(keys)
            stereo_frame_start.set(fs)
            stereo_frame_end.set(fe)
        except (ImageSetError, OSError):
            pass
        _rebuild_stereo_render_passes()

    def _schedule_stereo_range_refresh(*_):
        if stereo_refresh_after[0] is not None:
            try:
                root.after_cancel(stereo_refresh_after[0])
            except tk.TclError:
                pass
        stereo_refresh_after[0] = root.after(_RANGE_REFRESH_MS, _try_refresh_stereo_range)

    legacy_input_dir.trace_add("write", _schedule_legacy_range_refresh)
    legacy_ndisplay.trace_add("write", _schedule_legacy_range_refresh)
    legacy_input_naming.trace_add("write", _schedule_legacy_range_refresh)
    stereo_left_dir.trace_add("write", _schedule_stereo_range_refresh)
    stereo_right_dir.trace_add("write", _schedule_stereo_range_refresh)
    stereo_input_naming.trace_add("write", _schedule_stereo_range_refresh)

    def finish_job_ui(cancelled=False):
        pause_event.clear()
        set_buttons_idle()
        if cancelled:
            reset_progress_ui()
            progress_label.config(text="Cancelled")

    def open_dir_on_main(path):
        try:
            os.startfile(path)
        except OSError:
            pass

    def run_legacy_worker(max_workers, render_passes_to_process=None):
        start_time = time.time()
        cancelled = False
        err_title = err_msg = None
        out_to_open = None
        try:
            run_legacy_merger(
                legacy_input_dir.get(),
                legacy_ndisplay.get(),
                update_progressbar=update_progressbar,
                start_time=start_time,
                output_dir=legacy_output_dir.get().strip() or None,
                cancel_event=cancel_event,
                pause_event=pause_event,
                on_frame_status=update_frame_status,
                frame_start=legacy_frame_start.get().strip(),
                frame_end=legacy_frame_end.get().strip(),
                max_workers=max_workers,
                input_naming_template=legacy_input_naming.get().strip() or None,
                output_naming_template=legacy_output_naming.get().strip() or None,
                render_passes_to_process=render_passes_to_process,
            )
            cancelled = cancel_event.is_set()
            if not cancelled:
                out_to_open = legacy_output_dir.get().strip() or os.path.join(
                    legacy_input_dir.get(), "merged"
                )
        except ConfigError as exc:
            err_title, err_msg = "nDisplay Merger - Config Error", str(exc)
        except ImageSetError as exc:
            err_title, err_msg = "nDisplay Merger - Images Error", str(exc)
        except Exception as exc:
            err_title, err_msg = "nDisplay Merger - Error", str(exc)

        def ui_done():
            finish_job_ui(cancelled=cancelled)
            if err_title:
                reset_progress_ui()
                messagebox.showerror(err_title, err_msg)
            elif cancelled:
                pass
            else:
                if out_to_open:
                    open_dir_on_main(out_to_open)

        root.after(0, ui_done)

    def run_legacy():
        if not legacy_input_dir.get() or not legacy_ndisplay.get():
            messagebox.showerror(
                "nDisplay Merger",
                "Please provide valid paths for input directory and nDisplay config.",
            )
            return
        if not os.path.isdir(legacy_input_dir.get()) or not os.path.isfile(legacy_ndisplay.get()):
            messagebox.showerror(
                "nDisplay Merger",
                "Invalid paths provided. Please provide existing paths for input directory and nDisplay config.",
            )
            return
        if not legacy_frame_start.get().strip() or not legacy_frame_end.get().strip():
            messagebox.showerror(
                "nDisplay Merger",
                "Set start and end frame (they fill automatically when paths are valid).",
            )
            return
        nw = _parse_workers_ui(legacy_max_workers.get())
        if nw is None:
            messagebox.showerror(
                "nDisplay Merger",
                f"Workers must be an integer from 1 to {_MAX_UI_WORKERS}.",
            )
            return
        inp_tpl = (legacy_input_naming.get() or "").strip() or DEFAULT_LEGACY_INPUT
        rp_filter = None
        if input_template_uses_render_pass(inp_tpl):
            rp_filter = frozenset(p for p, var in legacy_rp_vars.items() if var.get())
            if not rp_filter:
                messagebox.showerror(
                    "nDisplay Merger",
                    "Select at least one render pass, or remove {render_pass} from the input naming template.",
                )
                return
        persist_settings()
        save_naming_and_render_passes_on_run()
        cancel_event.clear()
        pause_event.clear()
        reset_progress_ui()
        set_buttons_running()
        threading.Thread(
            target=run_legacy_worker,
            args=(nw, rp_filter),
            daemon=True,
        ).start()

    def run_stereo_worker(max_workers, render_passes_to_process=None):
        start_time = time.time()
        cancelled = False
        err_title = err_msg = None
        out_to_open = None
        left_p = stereo_left_dir.get()
        right_p = stereo_right_dir.get()
        try:
            stereo_mode = coerce_stereo_output_mode(
                _STEREO_MODE_LABEL_TO_VALUE.get(
                    stereo_mode_var.get(),
                    StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER.value,
                )
            )
            _persist_stereo_output_field_into_active_bucket()
            run_stereo_merger(
                left_p,
                right_p,
                output_dir=stereo_output_dir.get().strip() or None,
                update_progressbar=update_progressbar,
                start_time=start_time,
                cancel_event=cancel_event,
                pause_event=pause_event,
                on_frame_status=update_frame_status,
                frame_start=stereo_frame_start.get().strip(),
                frame_end=stereo_frame_end.get().strip(),
                max_workers=max_workers,
                output_mode=stereo_mode,
                input_naming_template=stereo_input_naming.get().strip() or None,
                output_naming_template=stereo_output_naming.get().strip() or None,
                render_passes_to_process=render_passes_to_process,
            )
            cancelled = cancel_event.is_set()
            if not cancelled:
                out_to_open = resolve_stereo_output_dir(left_p, stereo_output_dir.get().strip() or None)
        except ImageSetError as exc:
            err_title, err_msg = "Stereo VR Merger - Images Error", str(exc)
        except Exception as exc:
            err_title, err_msg = "Stereo VR Merger - Error", str(exc)

        def ui_done():
            finish_job_ui(cancelled=cancelled)
            if err_title:
                reset_progress_ui()
                messagebox.showerror(err_title, err_msg)
            elif cancelled:
                pass
            else:
                if out_to_open:
                    open_dir_on_main(out_to_open)

        root.after(0, ui_done)

    def run_stereo():
        if not stereo_left_dir.get() or not stereo_right_dir.get():
            messagebox.showerror(
                "Stereo VR Merger",
                "Please provide valid paths for left and right eye directories.",
            )
            return
        if not os.path.isdir(stereo_left_dir.get()) or not os.path.isdir(stereo_right_dir.get()):
            messagebox.showerror(
                "Stereo VR Merger",
                "Invalid paths: both eye directories must exist.",
            )
            return
        if not stereo_frame_start.get().strip() or not stereo_frame_end.get().strip():
            messagebox.showerror(
                "Stereo VR Merger",
                "Set start and end frame (they fill automatically when paths are valid).",
            )
            return
        nw = _parse_workers_ui(stereo_max_workers.get())
        if nw is None:
            messagebox.showerror(
                "Stereo VR Merger",
                f"Workers must be an integer from 1 to {_MAX_UI_WORKERS}.",
            )
            return
        inp_tpl = (stereo_input_naming.get() or "").strip() or DEFAULT_STEREO_INPUT
        rp_filter = None
        if input_template_uses_render_pass(inp_tpl):
            rp_filter = frozenset(p for p, var in stereo_rp_vars.items() if var.get())
            if not rp_filter:
                messagebox.showerror(
                    "Stereo VR Merger",
                    "Select at least one render pass, or remove {render_pass} from the input naming template.",
                )
                return
        persist_settings()
        save_naming_and_render_passes_on_run()
        cancel_event.clear()
        pause_event.clear()
        reset_progress_ui()
        set_buttons_running()
        threading.Thread(
            target=run_stereo_worker,
            args=(nw, rp_filter),
            daemon=True,
        ).start()

    def on_run_pause_resume():
        if not worker_running[0]:
            tab_idx = notebook.index(notebook.select())
            if tab_idx == 0:
                run_legacy()
            else:
                run_stereo()
        elif pause_event.is_set():
            on_resume()
        else:
            on_pause()

    run_pause_resume_btn.config(command=on_run_pause_resume)

    def on_closing():
        persist_settings()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    def _startup_range_refresh():
        _try_refresh_legacy_range()
        _try_refresh_stereo_range()

    root.after(100, _startup_range_refresh)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    root = TkinterDnD.Tk()
    root.title("nDisplay Merger")
    _apply_window_icon(root)
    root.resizable(False, False)
    build_ui(root)
    root.mainloop()
