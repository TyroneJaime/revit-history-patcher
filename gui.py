"""
gui.py — Tkinter GUI for the RVT History Patcher.
Run this file directly: python gui.py
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import patcher


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Revit History Patcher")
        self.resizable(False, False)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # --- File selection ---
        file_frame = ttk.LabelFrame(self, text="Source File", padding=8)
        file_frame.grid(row=0, column=0, sticky="ew", **pad)

        self._rvt_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self._rvt_var, width=55).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(file_frame, text="Browse…", command=self._browse_rvt).grid(row=0, column=1)
        ttk.Button(file_frame, text="Detect username", command=self._detect).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        # --- Username fields ---
        name_frame = ttk.LabelFrame(self, text="Usernames", padding=8)
        name_frame.grid(row=1, column=0, sticky="ew", **pad)

        ttk.Label(name_frame, text="Current username:").grid(row=0, column=0, sticky="w")
        self._old_var = tk.StringVar()
        ttk.Entry(name_frame, textvariable=self._old_var, width=30).grid(row=0, column=1, padx=6)
        self._old_info = ttk.Label(name_frame, text="", foreground="gray")
        self._old_info.grid(row=0, column=2, sticky="w")

        ttk.Label(name_frame, text="New username:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._new_var = tk.StringVar()
        self._new_entry = ttk.Entry(name_frame, textvariable=self._new_var, width=30)
        self._new_entry.grid(row=1, column=1, padx=6, pady=(6, 0))
        self._len_label = ttk.Label(name_frame, text="", foreground="gray")
        self._len_label.grid(row=1, column=2, sticky="w", pady=(6, 0))

        self._new_var.trace_add("write", self._on_new_changed)

        # --- Output ---
        out_frame = ttk.LabelFrame(self, text="Output", padding=8)
        out_frame.grid(row=2, column=0, sticky="ew", **pad)

        self._overwrite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            out_frame,
            text="Patch in-place (overwrite source file)",
            variable=self._overwrite_var,
            command=self._toggle_output,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(out_frame, text="Save as:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._out_var = tk.StringVar()
        self._out_entry = ttk.Entry(out_frame, textvariable=self._out_var, width=45, state="disabled")
        self._out_entry.grid(row=1, column=1, padx=6, pady=(4, 0))
        self._out_browse = ttk.Button(out_frame, text="Browse…", command=self._browse_out, state="disabled")
        self._out_browse.grid(row=1, column=2, pady=(4, 0))

        # --- Patch button ---
        self._patch_btn = ttk.Button(self, text="Patch", command=self._patch, width=20)
        self._patch_btn.grid(row=3, column=0, pady=6)

        # --- Log ---
        log_frame = ttk.LabelFrame(self, text="Log", padding=6)
        log_frame.grid(row=4, column=0, sticky="nsew", **pad)
        self._log = scrolledtext.ScrolledText(log_frame, width=72, height=12, state="disabled", font=("Consolas", 9))
        self._log.pack()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _browse_rvt(self):
        path = filedialog.askopenfilename(filetypes=[("Revit files", "*.rvt"), ("All files", "*.*")])
        if path:
            self._rvt_var.set(path)

    def _browse_out(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".rvt",
            filetypes=[("Revit files", "*.rvt"), ("All files", "*.*")],
        )
        if path:
            self._out_var.set(path)

    def _toggle_output(self):
        state = "disabled" if self._overwrite_var.get() else "normal"
        self._out_entry.config(state=state)
        self._out_browse.config(state=state)

    def _on_new_changed(self, *_):
        new = self._new_var.get()
        self._len_label.config(text=f"{len(new)} chars", foreground="gray")

    def _detect(self):
        path = self._rvt_var.get().strip()
        if not path:
            messagebox.showwarning("No file", "Select an RVT file first.")
            return
        self._log_write("Detecting usernames...\n")
        try:
            results = patcher.detect_usernames(path)
        except Exception as e:
            self._log_write(f"Error: {e}\n")
            return
        if not results:
            self._log_write("No usernames found in stream.\n")
            return
        for name, count in results:
            self._log_write(f"  '{name}'  —  {count} save entries\n")
        top_name, top_count = results[0]
        self._old_var.set(top_name)
        self._old_info.config(text=f"({top_count} entries)")
        self._on_new_changed()

    def _patch(self):
        rvt = self._rvt_var.get().strip()
        old = self._old_var.get().strip()
        new = self._new_var.get().strip()

        if not rvt:
            messagebox.showwarning("Missing field", "Select an RVT file.")
            return
        if not old:
            messagebox.showwarning("Missing field", "Enter the current username (or click Detect).")
            return
        if not new:
            messagebox.showwarning("Missing field", "Enter the new username.")
            return

        out = None if self._overwrite_var.get() else self._out_var.get().strip()
        if not self._overwrite_var.get() and not out:
            messagebox.showwarning("Missing field", "Choose an output file or enable in-place patching.")
            return

        self._patch_btn.config(state="disabled")
        self._log_write("\n--- Starting patch ---\n")
        thread = threading.Thread(target=self._run_patch, args=(rvt, old, new, out), daemon=True)
        thread.start()

    def _run_patch(self, rvt, old, new, out):
        try:
            result = patcher.patch_rvt(rvt, old, new, output_path=out, log=self._log_write)
            self._log_write(
                f"\n✓ Patch complete — {result['replaced']} entries updated, "
                f"{result['stream_bytes']} bytes written.\n"
            )
        except Exception as e:
            self._log_write(f"\n✗ Error: {e}\n")
        finally:
            self.after(0, lambda: self._patch_btn.config(state="normal"))

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_write(self, text: str):
        def _do():
            self._log.config(state="normal")
            self._log.insert("end", text)
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)


if __name__ == "__main__":
    app = App()
    app.mainloop()
