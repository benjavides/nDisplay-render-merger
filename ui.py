import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import TkinterDnD

from errors import ConfigError, ImageSetError
from nDisplayMerger import main as run_legacy_merger
from stereo_merger import main as run_stereo_merger, resolve_stereo_output_dir

SETTINGS_PATH = os.path.join(os.getcwd(), "settings.json")

HELP_LEGACY = (
    "Standard Config Merger takes rendered nDisplay viewports and places them on a canvas "
    "according to the Output Mapping in the provided .ndisplay config.\n\n"
    "Assumptions: files must be named so the viewport name and frame number can be parsed, "
    "for example:\n{LevelSequence}.{ViewportName}.{FrameNumber}.jpeg"
)

HELP_STEREO = (
    "Stereo VR Merger converts 6 cubemap faces per eye into an equirectangular projection "
    "and stacks them over-under (left eye on top, right eye on bottom).\n\n"
    "Assumptions:\n"
    "• Both input folders must contain the same temporal frames.\n"
    "• Viewport names must include these face identifiers: BACK, LEFT, FRONT, RIGHT, UP, DOWN "
    "(matched as separate tokens; case-insensitive).\n"
    "• All 6 face images for a frame must be square and the same resolution."
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

    settings = load_settings()

    legacy_input_dir = tk.StringVar(value=settings.get("legacy_input_dir", ""))
    legacy_ndisplay = tk.StringVar(value=settings.get("legacy_ndisplay", ""))
    legacy_output_dir = tk.StringVar(value=settings.get("legacy_output_dir", ""))

    stereo_left_dir = tk.StringVar(value=settings.get("stereo_left_dir", ""))
    stereo_right_dir = tk.StringVar(value=settings.get("stereo_right_dir", ""))
    stereo_output_dir = tk.StringVar(value=settings.get("stereo_output_dir", ""))

    def persist_settings():
        save_settings(
            {
                "legacy_input_dir": legacy_input_dir.get(),
                "legacy_ndisplay": legacy_ndisplay.get(),
                "legacy_output_dir": legacy_output_dir.get(),
                "stereo_left_dir": stereo_left_dir.get(),
                "stereo_right_dir": stereo_right_dir.get(),
                "stereo_output_dir": stereo_output_dir.get(),
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
    footer.columnconfigure(1, weight=1)

    stop_btn = ttk.Button(footer, text="Stop", state=tk.DISABLED)
    stop_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))

    progressbar = ttk.Progressbar(footer, orient=tk.HORIZONTAL, length=300, mode="determinate")
    progressbar.grid(row=0, column=1, sticky="ew")

    progress_label = tk.Label(footer, text="")
    progress_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

    run_legacy_btn = None
    run_stereo_btn = None

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

    def reset_progress_ui():
        progressbar["value"] = 0
        progressbar["maximum"] = 0
        progress_label.config(text="")

    def on_stop():
        cancel_event.set()

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

    run_legacy_btn = ttk.Button(tab_legacy, text="Run")
    run_legacy_btn.grid(row=row, column=1, pady=(4, 8), sticky="w")
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

    run_stereo_btn = ttk.Button(tab_stereo, text="Run")
    run_stereo_btn.grid(row=row, column=1, pady=(4, 8), sticky="w")

    def set_running(running):
        state_run = tk.DISABLED if running else tk.NORMAL
        state_stop = tk.NORMAL if running else tk.DISABLED
        run_legacy_btn.config(state=state_run)
        run_stereo_btn.config(state=state_run)
        stop_btn.config(state=state_stop)

    def finish_job_ui(cancelled=False):
        set_running(False)
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
        persist_settings()
        cancel_event.clear()
        reset_progress_ui()
        set_running(True)
        threading.Thread(target=run_legacy_worker, daemon=True).start()

    run_legacy_btn.config(command=run_legacy)

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
        persist_settings()
        cancel_event.clear()
        reset_progress_ui()
        set_running(True)
        threading.Thread(target=run_stereo_worker, daemon=True).start()

    run_stereo_btn.config(command=run_stereo)

    def on_closing():
        persist_settings()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.title("nDisplay Merger")
    root.resizable(False, False)
    build_ui(root)
    root.mainloop()
