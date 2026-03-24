"""Tk Entry with `{` keyword completion dropdown for naming scheme fields."""
import tkinter as tk
from tkinter import ttk


class NamingSchemeEntry(ttk.Frame):
    def __init__(self, master, textvariable, keywords, width=50, **kw):
        super().__init__(master, **kw)
        self.textvariable = textvariable
        self.keywords = tuple(sorted(keywords))
        self._popup = None
        self._listbox = None
        self._filter_prefix = ""

        self.entry = ttk.Entry(self, textvariable=textvariable, width=width)
        self.entry.pack(fill="x", expand=True)
        self.entry.bind("<KeyRelease>", self._on_keyrelease)
        self.entry.bind("<Escape>", lambda e: self._hide_popup())

    def _hide_popup(self):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None
            self._listbox = None

    def _filtered_keywords(self, prefix: str):
        p = prefix.lower()
        return [k for k in self.keywords if k.lower().startswith(p)]

    def _on_keyrelease(self, event):
        if event.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return
        if event.keysym == "Escape":
            self._hide_popup()
            return

        content = self.textvariable.get()
        cursor = self.entry.index("insert")

        before = content[:cursor]
        open_idx = before.rfind("{")
        close_idx = before.rfind("}")
        if open_idx < 0 or (close_idx > open_idx):
            self._hide_popup()
            return

        partial = before[open_idx + 1 :]
        if "}" in partial or "/" in partial or "\\" in partial:
            self._hide_popup()
            return

        self._filter_prefix = partial
        matches = self._filtered_keywords(partial)
        if not matches:
            self._hide_popup()
            return

        self._show_popup(matches, open_idx, partial)

    def _show_popup(self, matches, brace_pos, partial):
        self._hide_popup()
        self._popup = tk.Toplevel(self)
        self._popup.wm_overrideredirect(True)
        self._popup.attributes("-topmost", True)

        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        self._popup.geometry(f"+{x}+{y}")

        lb = tk.Listbox(self._popup, height=min(12, len(matches)), width=28, exportselection=False)
        lb.pack(fill="both", expand=True)
        for m in matches:
            lb.insert("end", m)
        self._listbox = lb

        def apply_selection(_event=None):
            sel = lb.curselection()
            if not sel:
                return
            word = lb.get(sel[0])
            content = self.textvariable.get()
            cursor = self.entry.index("insert")
            before = content[:cursor]
            after = content[cursor:]
            open_idx = before.rfind("{")
            new_before = before[: open_idx + 1] + word + "}"
            self.textvariable.set(new_before + after)
            new_cursor = len(new_before)
            self.entry.icursor(new_cursor)
            self._hide_popup()

        lb.bind("<Return>", apply_selection)
        lb.bind("<Double-Button-1>", apply_selection)

        def on_lb_release(_event=None):
            if lb.curselection():
                apply_selection()

        lb.bind("<ButtonRelease-1>", on_lb_release)

        lb.focus_set()
        if matches:
            lb.selection_set(0)
