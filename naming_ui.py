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

        self.entry = ttk.Entry(self, textvariable=textvariable, width=width)
        self.entry.pack(fill="x", expand=True)
        self.entry.bind("<KeyPress>", self._on_keypress)
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

    def _apply_popup_selection(self, _event=None):
        if self._listbox is None:
            return
        lb = self._listbox
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
        self.entry.focus_set()

    def _completion_keynav(self, event):
        """Up/Down/Enter while popup is open; keep focus on the entry."""
        if self._listbox is None:
            return
        lb = self._listbox
        n = lb.size()
        if n == 0:
            return (
                "break"
                if event.keysym in ("Down", "Up", "Return", "KP_Enter", "Tab")
                else None
            )

        if event.keysym in ("Return", "KP_Enter", "Tab"):
            self._apply_popup_selection()
            return "break"

        if event.keysym == "Down":
            sel = lb.curselection()
            idx = int(sel[0]) if sel else 0
            idx = min(idx + 1, n - 1)
            lb.selection_clear(0, "end")
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)
            return "break"

        if event.keysym == "Up":
            sel = lb.curselection()
            idx = int(sel[0]) if sel else 0
            idx = max(idx - 1, 0)
            lb.selection_clear(0, "end")
            lb.selection_set(idx)
            lb.activate(idx)
            lb.see(idx)
            return "break"

        return None

    def _on_keypress(self, event):
        return self._completion_keynav(event)

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

        matches = self._filtered_keywords(partial)
        if not matches:
            self._hide_popup()
            return

        prev_sel = None
        if self._listbox is not None:
            try:
                s = self._listbox.curselection()
                if s:
                    prev_sel = self._listbox.get(s[0])
            except tk.TclError:
                pass

        self._show_popup(matches)

        if prev_sel is not None and self._listbox is not None:
            lb = self._listbox
            for i in range(lb.size()):
                if lb.get(i) == prev_sel:
                    lb.selection_clear(0, "end")
                    lb.selection_set(i)
                    lb.activate(i)
                    lb.see(i)
                    break

    def _show_popup(self, matches):
        self._hide_popup()
        self._popup = tk.Toplevel(self)
        self._popup.wm_overrideredirect(True)
        self._popup.attributes("-topmost", True)

        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        self._popup.geometry(f"+{x}+{y}")

        lb = tk.Listbox(
            self._popup,
            height=min(12, len(matches)),
            width=28,
            exportselection=False,
            takefocus=False,
        )
        lb.pack(fill="both", expand=True)
        for m in matches:
            lb.insert("end", m)
        self._listbox = lb

        def on_lb_release(_event=None):
            if lb.curselection():
                self._apply_popup_selection()

        lb.bind("<Return>", lambda e: self._apply_popup_selection())
        lb.bind("<Double-Button-1>", lambda e: self._apply_popup_selection())
        lb.bind("<ButtonRelease-1>", on_lb_release)

        # Keep typing in the entry; do not focus the listbox (would steal key events).

        lb.selection_set(0)
        lb.activate(0)
