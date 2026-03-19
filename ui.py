import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import TkinterDnD

from errors import ConfigError, ImageSetError
from nDisplayMerger import list_legacy_frame_keys, main as run_legacy_merger
from stereo_merger import (
    list_paired_stereo_frames,
    main as run_stereo_merger,
    resolve_stereo_output_dir,
)

SETTINGS_PATH = os.path.join(os.getcwd(), "settings.json")
_RANGE_REFRESH_MS = 400

HELP_LEGACY = (
    "Standard Config Merger takes rendered nDisplay viewports and places them on a canvas "
    "according to the Output Mapping in the provided .ndisplay config.\n\n"
    "Assumptions: files must be named so the viewport name and frame number can be parsed, "
    "for example:\n{LevelSequence}.{ViewportName}.{FrameNumber}.jpeg\n\n"
    "Pause may occur while a frame is being built; when you resume, that frame is processed "
    "again from the start. Start/End frame (inclusive integers) limit which frames are exported; "
    "they update automatically when input and config paths are valid. "
    "While running, the status line shows e.g. “Merging frame 15 (1/11)” — frame id and batch progress. "
    "Use Run / Pause / Resume next to Stop (footer) to control the job for the active tab.\n\n"
    "Frames merge in parallel (one process per CPU, up to 16). Pause applies between frames "
    "while workers finish; use CLI --jobs 1 for fully sequential mid-frame pause."
)

HELP_STEREO = (
    "Stereo VR Merger converts 6 cubemap faces per eye into an equirectangular projection "
    "and stacks them over-under (left eye on top, right eye on bottom).\n\n"
    "Assumptions:\n"
    "• Both input folders must contain the same temporal frames.\n"
    "• Viewport names must include these face identifiers: BACK, LEFT, FRONT, RIGHT, UP, DOWN "
    "(matched as separate tokens; case-insensitive).\n"
    "• All 6 face images for a frame must be square and the same resolution.\n\n"
    "Pause may occur while a frame is being built; when you resume, that frame is processed "
    "again from the start. Start/End frame (inclusive integers) limit export; they update when "
    "both eye folders are valid. While running, status shows e.g. “Merging frame 15 (1/11)”. "
    "Run / Pause / Resume is next to Stop in the footer.\n\n"
    "Frames process in parallel (CPU-based). Pause takes effect between frames. CLI: --jobs 1 for sequential."
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


def _norm_drop_path(data):
    path = os.path.normpath(data.strip("{}"))
    return os.path.abspath(path)


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

    worker_running = [False]

    def persist_settings():
        save_settings(
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
            }
        )

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

    # --- debounced range refresh ---
    legacy_refresh_after = [None]
    stereo_refresh_after = [None]

    def _try_refresh_legacy_range():
        legacy_refresh_after[0] = None
        inp = legacy_input_dir.get().strip()
        cfg = legacy_ndisplay.get().strip()
        if not inp or not cfg or not os.path.isdir(inp) or not os.path.isfile(cfg):
            return
        try:
            keys = list_legacy_frame_keys(inp, cfg)
            legacy_frame_start.set(str(keys[0]))
            legacy_frame_end.set(str(keys[-1]))
        except (ConfigError, ImageSetError, OSError):
            pass

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
            return
        try:
            keys = list_paired_stereo_frames(left_p, right_p)
            stereo_frame_start.set(str(keys[0]))
            stereo_frame_end.set(str(keys[-1]))
        except (ImageSetError, OSError):
            pass

    def _schedule_stereo_range_refresh(*_):
        if stereo_refresh_after[0] is not None:
            try:
                root.after_cancel(stereo_refresh_after[0])
            except tk.TclError:
                pass
        stereo_refresh_after[0] = root.after(_RANGE_REFRESH_MS, _try_refresh_stereo_range)

    legacy_input_dir.trace_add("write", _schedule_legacy_range_refresh)
    legacy_ndisplay.trace_add("write", _schedule_legacy_range_refresh)
    stereo_left_dir.trace_add("write", _schedule_stereo_range_refresh)
    stereo_right_dir.trace_add("write", _schedule_stereo_range_refresh)

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

    stereo_hint = tk.Label(
        tab_stereo,
        text="If left empty, output goes to 'merged_stereo' next to the left eye folder's parent path.",
        fg="gray",
    )
    stereo_hint.grid(row=row, column=1, sticky="w", pady=(0, 6))
    row += 1

    def _on_stereo_out_change(*_):
        if stereo_output_dir.get().strip():
            stereo_hint.grid_remove()
        else:
            stereo_hint.grid()

    stereo_output_dir.trace_add("write", _on_stereo_out_change)
    _on_stereo_out_change()

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

    def finish_job_ui(cancelled=False):
        pause_event.clear()
        set_buttons_idle()
        if cancelled:
            progress_label.config(text="Cancelled")

    def open_dir_on_main(path):
        try:
            os.startfile(path)
        except OSError:
            pass

    def run_legacy_worker():
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
        persist_settings()
        cancel_event.clear()
        pause_event.clear()
        reset_progress_ui()
        set_buttons_running()
        threading.Thread(target=run_legacy_worker, daemon=True).start()

    def run_stereo_worker():
        start_time = time.time()
        cancelled = False
        err_title = err_msg = None
        out_to_open = None
        left_p = stereo_left_dir.get()
        right_p = stereo_right_dir.get()
        try:
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
        persist_settings()
        cancel_event.clear()
        pause_event.clear()
        reset_progress_ui()
        set_buttons_running()
        threading.Thread(target=run_stereo_worker, daemon=True).start()

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
    root.resizable(False, False)
    build_ui(root)
    root.mainloop()
