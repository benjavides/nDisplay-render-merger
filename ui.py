import tkinter as tk
from tkinter import filedialog
from tkinterdnd2 import TkinterDnD
from nDisplayMerger import main
import os
import subprocess

def on_drop_input_dir(event):
    path = os.path.normpath(event.data.strip('{}'))
    abs_path = os.path.abspath(path)
    rel_path = os.path.relpath(abs_path, os.path.dirname(os.path.abspath(__file__)))
    input_dir.set(path)

def on_drop_ndisplay_config(event):
    path = os.path.normpath(event.data.strip('{}'))
    abs_path = os.path.abspath(path)
    rel_path = os.path.relpath(abs_path, os.path.dirname(os.path.abspath(__file__)))
    ndisplay_config_path.set(path)

def browse_input_dir():
    abs_path = filedialog.askdirectory()
    rel_path = os.path.relpath(abs_path, os.path.dirname(os.path.abspath(__file__)))
    input_dir.set(abs_path)

def browse_ndisplay_config():
    abs_path = filedialog.askopenfilename(filetypes=[("nDisplay Config Files", "*.ndisplay")])
    rel_path = os.path.relpath(abs_path, os.path.dirname(os.path.abspath(__file__)))
    ndisplay_config_path.set(abs_path)

def run_compositor():
    main(input_dir.get(), ndisplay_config_path.get())
    
    output_dir = os.path.join(input_dir.get(), "merged")
    os.startfile(output_dir)  # Open the output folder in Windows
    root.destroy()  # Close the UI


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    root.title("Pi nDisplay Image Compositor")

    input_dir = tk.StringVar()
    ndisplay_config_path = tk.StringVar()

    tk.Label(root, text="Input Directory:").grid(row=0, column=0, sticky="e")
    input_dir_entry = tk.Entry(root, textvariable=input_dir, width=50)
    input_dir_entry.grid(row=0, column=1)
    input_dir_entry.drop_target_register("DND_Files")
    input_dir_entry.dnd_bind("<<Drop>>", on_drop_input_dir)
    tk.Button(root, text="Browse", command=browse_input_dir).grid(row=0, column=2)

    tk.Label(root, text="nDisplay Config:").grid(row=1, column=0, sticky="e")
    ndisplay_config_entry = tk.Entry(root, textvariable=ndisplay_config_path, width=50)
    ndisplay_config_entry.grid(row=1, column=1)
    ndisplay_config_entry.drop_target_register("DND_Files")
    ndisplay_config_entry.dnd_bind("<<Drop>>", on_drop_ndisplay_config)
    tk.Button(root, text="Browse", command=browse_ndisplay_config).grid(row=1, column=2)

    tk.Button(root, text="Run Compositor", command=run_compositor).grid(row=2, column=1, pady=10)

    root.mainloop()