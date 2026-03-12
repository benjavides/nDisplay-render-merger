import tkinter as tk
from tkinter import filedialog, ttk, messagebox

from tkinterdnd2 import TkinterDnD

from errors import ConfigError, ImageSetError
from nDisplayMerger import main
import os
import time
import threading


def on_drop_input_dir(event):
    path = os.path.normpath(event.data.strip("{}"))
    abs_path = os.path.abspath(path)
    input_dir.set(abs_path)


def on_drop_ndisplay_config(event):
    path = os.path.normpath(event.data.strip("{}"))
    abs_path = os.path.abspath(path)
    ndisplay_config_path.set(abs_path)


def browse_input_dir():
    abs_path = filedialog.askdirectory()
    input_dir.set(abs_path)


def browse_ndisplay_config():
    abs_path = filedialog.askopenfilename(filetypes=[("nDisplay Config Files", "*.ndisplay")])
    ndisplay_config_path.set(abs_path)


def browse_output_dir():
    abs_path = filedialog.askdirectory()
    output_dir.set(abs_path)


def _format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"

    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {sec}s"


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
    # Ensure all Tkinter widget updates happen on the main thread
    try:
        root.after(0, _update_progressbar_ui, value, max_value, start_time)
    except RuntimeError:
        # In case the root window is already destroyed while a background
        # thread is still trying to report progress, just ignore the update.
        pass


def run_compositor():
    if not input_dir.get() or not ndisplay_config_path.get():
        messagebox.showerror(
            "nDisplay Merger",
            "Please provide valid paths for input directory and nDisplay config.",
        )
        return

    # Check if the provided paths exist
    if not os.path.isdir(input_dir.get()) or not os.path.isfile(ndisplay_config_path.get()):
        messagebox.showerror(
            "nDisplay Merger",
            "Invalid paths provided. Please provide existing paths for input directory and nDisplay config.",
        )
        return

    start_time = time.time()
    try:
        main(
            input_dir.get(),
            ndisplay_config_path.get(),
            update_progressbar=update_progressbar,
            start_time=start_time,
            output_dir=output_dir.get().strip() or None,
        )
    except ConfigError as exc:
        messagebox.showerror("nDisplay Merger - Config Error", str(exc))
        return
    except ImageSetError as exc:
        messagebox.showerror("nDisplay Merger - Images Error", str(exc))
        return
    except Exception as exc:
        messagebox.showerror("nDisplay Merger - Error", str(exc))
        return

    final_output_dir = output_dir.get().strip() or os.path.join(input_dir.get(), "merged")
    os.startfile(final_output_dir)  # Open the output folder in Windows


def run_compositor_thread():
    run_thread = threading.Thread(target=run_compositor, daemon=True)
    run_thread.start()


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.title("nDisplay Merger")
    root.resizable(False, False)

    # Content frame with consistent padding
    main_frame = ttk.Frame(root, padding=(12, 12, 12, 12))
    main_frame.grid(row=0, column=0, sticky="nsew")

    # Let the middle column expand horizontally
    main_frame.columnconfigure(0, weight=0)
    main_frame.columnconfigure(1, weight=1)
    main_frame.columnconfigure(2, weight=0)

    input_dir = tk.StringVar()
    ndisplay_config_path = tk.StringVar()
    output_dir = tk.StringVar()

    row_pad_y = 4

    tk.Label(main_frame, text="Input Directory:").grid(
        row=0, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    input_dir_entry = tk.Entry(main_frame, textvariable=input_dir, width=50)
    input_dir_entry.grid(row=0, column=1, sticky="ew", pady=row_pad_y)
    input_dir_entry.drop_target_register("DND_Files")
    input_dir_entry.dnd_bind("<<Drop>>", on_drop_input_dir)
    tk.Button(main_frame, text="Browse", command=browse_input_dir).grid(
        row=0, column=2, padx=(8, 0), pady=row_pad_y
    )

    tk.Label(main_frame, text="nDisplay Config:").grid(
        row=1, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    ndisplay_config_entry = tk.Entry(main_frame, textvariable=ndisplay_config_path, width=50)
    ndisplay_config_entry.grid(row=1, column=1, sticky="ew", pady=row_pad_y)
    ndisplay_config_entry.drop_target_register("DND_Files")
    ndisplay_config_entry.dnd_bind("<<Drop>>", on_drop_ndisplay_config)
    tk.Button(main_frame, text="Browse", command=browse_ndisplay_config).grid(
        row=1, column=2, padx=(8, 0), pady=row_pad_y
    )

    tk.Label(main_frame, text="Output Directory (optional):").grid(
        row=2, column=0, sticky="e", padx=(0, 8), pady=row_pad_y
    )
    output_dir_entry = tk.Entry(main_frame, textvariable=output_dir, width=50)
    output_dir_entry.grid(row=2, column=1, sticky="ew", pady=row_pad_y)
    tk.Button(main_frame, text="Browse", command=browse_output_dir).grid(
        row=2, column=2, padx=(8, 0), pady=row_pad_y
    )

    default_output_hint = tk.Label(
        main_frame,
        text="If left empty, output will be saved to a 'merged' folder inside the input directory.",
        fg="gray",
    )
    default_output_hint.grid(row=3, column=1, sticky="w", pady=(0, 6))

    def _on_output_dir_change(*args):
        if output_dir.get().strip():
            default_output_hint.grid_remove()
        else:
            default_output_hint.grid()

    output_dir.trace_add("write", _on_output_dir_change)

    tk.Button(main_frame, text="Run Compositor", command=run_compositor_thread).grid(
        row=4, column=1, pady=(4, 8)
    )

    progressbar = ttk.Progressbar(main_frame, orient=tk.HORIZONTAL, length=300, mode="determinate")
    progressbar.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 4))

    progress_label = tk.Label(main_frame, text="")
    progress_label.grid(row=6, column=0, columnspan=3, sticky="w")

    root.mainloop()