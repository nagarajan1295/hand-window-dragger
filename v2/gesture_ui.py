"""Dialog for recording a new custom gesture and mapping it to an action."""

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from actions import ACTIONS
from gesture_templates import GestureRecorder, load_templates, save_templates

PREVIEW_W, PREVIEW_H = 480, 270


class AddGestureDialog(tk.Toplevel):
    def __init__(self, parent, camera_index, mirror, on_saved):
        super().__init__(parent)
        self.title("Add Custom Gesture")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.camera_index = camera_index
        self.mirror = mirror
        self.on_saved = on_saved
        self.recorder = None
        self.frame_queue = queue.Queue(maxsize=1)
        self._final_features = None
        self._action_keys = list(ACTIONS.keys())

        self._build_setup_ui()

    def _build_setup_ui(self):
        for w in self.winfo_children():
            w.destroy()
        pad = ttk.Frame(self, padding=16)
        pad.grid(row=0, column=0)

        ttk.Label(pad, text="Gesture name:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(pad, textvariable=self.name_var, width=32).grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(pad, text="Action:").grid(row=2, column=0, sticky="w")
        labels = [label for label, _fn in ACTIONS.values()]
        self.combo = ttk.Combobox(pad, values=labels, state="readonly", width=34)
        self.combo.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.combo.current(0)

        ttk.Label(
            pad, justify="left",
            text="Hold a distinct hand pose (not a fist -- that's reserved\n"
                 "for grabbing windows) steady for about 1.5 seconds once\n"
                 "recording starts.",
        ).grid(row=4, column=0, sticky="w", pady=(0, 10))

        btn_row = ttk.Frame(pad)
        btn_row.grid(row=5, column=0, sticky="ew")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="Start Recording", command=self._start_recording).pack(side="right")

    def _start_recording(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Give the gesture a name first.", parent=self)
            return
        self._chosen_name = name
        self._chosen_action = self._action_keys[self.combo.current()]
        self._final_features = None

        for w in self.winfo_children():
            w.destroy()
        pad = ttk.Frame(self, padding=16)
        pad.grid(row=0, column=0)
        self.status_var = tk.StringVar(value="Get ready...")
        ttk.Label(pad, textvariable=self.status_var, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        self.preview_label = ttk.Label(pad)
        self.preview_label.grid(row=1, column=0)
        blank = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (30, 30, 30))
        self._blank_img = ImageTk.PhotoImage(blank)
        self.preview_label.configure(image=self._blank_img)
        self.progress_bar = ttk.Progressbar(pad, length=PREVIEW_W, maximum=1.0)
        self.progress_bar.grid(row=2, column=0, sticky="ew", pady=(10, 10))
        ttk.Button(pad, text="Cancel", command=self._cancel_recording).grid(row=3, column=0, sticky="e")

        self.recorder = GestureRecorder(
            self.camera_index, mirror=self.mirror,
            on_frame=self._on_frame, on_complete=self._on_complete, on_event=lambda m: None,
        )
        self.recorder.start()
        self._poll()

    def _on_frame(self, frame, info):
        try:
            self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frame_queue.put_nowait((frame, info))
        except queue.Full:
            pass

    def _on_complete(self, features):
        self._final_features = features
        self._recording_done = True

    def _poll(self):
        try:
            while True:
                frame, info = self.frame_queue.get_nowait()
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb).resize((PREVIEW_W, PREVIEW_H))
                photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=photo)
                self.preview_label.image = photo
                self.status_var.set("Get ready..." if info["phase"] == "warmup" else "Hold steady -- recording...")
                self.progress_bar["value"] = info["progress"]
        except queue.Empty:
            pass

        if self.recorder and not self.recorder.running and getattr(self, "_recording_done", False):
            if self._final_features is None:
                messagebox.showinfo("No hand detected", "Didn't see a hand -- try again.", parent=self)
                self._build_setup_ui()
                return
            templates = load_templates()
            key = f"gesture_{int(time.time() * 1000)}"
            templates.append({
                "key": key,
                "name": self._chosen_name,
                "action": self._chosen_action,
                "features": self._final_features,
            })
            save_templates(templates)
            if self.on_saved:
                self.on_saved()
            self.destroy()
            return

        self.after(15, self._poll)

    def _cancel_recording(self):
        if self.recorder:
            threading.Thread(target=self.recorder.stop, daemon=True).start()
        self.destroy()

    def _on_close(self):
        if self.recorder and self.recorder.running:
            threading.Thread(target=self.recorder.stop, daemon=True).start()
        self.destroy()
