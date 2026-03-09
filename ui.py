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


def _update_progressbar_ui(value, max_value, start_time):
    progressbar["value"] = value
    progressbar["maximum"] = max_value

    if value <= 0 or max_value <= 0:
        progress_label.config(text="")
        return

    elapsed_time = time.time() - start_time
    remaining_time = (max_value - value) * (elapsed_time / value)
    progress_label.config(text=f"Time remaining: {int(remaining_time)} seconds")


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
        main(input_dir.get(), ndisplay_config_path.get(), update_progressbar, start_time)
    except ConfigError as exc:
        messagebox.showerror("nDisplay Merger - Config Error", str(exc))
        return
    except ImageSetError as exc:
        messagebox.showerror("nDisplay Merger - Images Error", str(exc))
        return
    except Exception as exc:
        messagebox.showerror("nDisplay Merger - Error", str(exc))
        return

    output_dir = os.path.join(input_dir.get(), "merged")
    os.startfile(output_dir)  # Open the output folder in Windows
    root.destroy()  # Close the UI


def run_compositor_thread():
    run_thread = threading.Thread(target=run_compositor, daemon=True)
    run_thread.start()


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.title("nDisplay Merger")

    input_dir = tk.StringVar()
    ndisplay_config_path = tk.StringVar()

    tk.Label(root, text="Input Directory:").grid(row=0, column=0, sticky="e", padx=10, pady=10)
    input_dir_entry = tk.Entry(root, textvariable=input_dir, width=50)
    input_dir_entry.grid(row=0, column=1)
    input_dir_entry.drop_target_register("DND_Files")
    input_dir_entry.dnd_bind("<<Drop>>", on_drop_input_dir)
    tk.Button(root, text="Browse", command=browse_input_dir).grid(row=0, column=2, padx=10, pady=10)

    tk.Label(root, text="nDisplay Config:").grid(row=1, column=0, sticky="e")
    ndisplay_config_entry = tk.Entry(root, textvariable=ndisplay_config_path, width=50)
    ndisplay_config_entry.grid(row=1, column=1)
    ndisplay_config_entry.drop_target_register("DND_Files")
    ndisplay_config_entry.dnd_bind("<<Drop>>", on_drop_ndisplay_config)
    tk.Button(root, text="Browse", command=browse_ndisplay_config).grid(row=1, column=2)

    tk.Button(root, text="Run Compositor", command=run_compositor_thread).grid(row=2, column=1, pady=10)

    progressbar = ttk.Progressbar(root, orient=tk.HORIZONTAL, length=300, mode="determinate")
    progressbar.grid(row=3, column=1, pady=5)

    progress_label = tk.Label(root, text="")
    progress_label.grid(row=4, column=1)

    root.mainloop()