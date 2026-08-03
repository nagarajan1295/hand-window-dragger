"""Dialog for recording a new custom gesture and mapping it to an action:
a built-in action, a recorded keyboard shortcut, or a recorded mouse
click position."""

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import win32api
from PIL import Image, ImageTk

from actions import ACTIONS
from gesture_templates import GestureRecorder, load_templates, save_templates
from key_capture import ComboRecorder

PREVIEW_W, PREVIEW_H = 480, 270

_MOUSE_CLICK_KINDS = {
    "Single Left Click": ("left", 1),
    "Double Left Click": ("left", 2),
    "Single Right Click": ("right", 1),
}


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
        self._recording_done = False
        self._action_keys = list(ACTIONS.keys())
        self._captured_combo = None       # (vk_codes, display_str)
        self._captured_mouse_pos = None    # (x, y)
        self._mouse_countdown_job = None

        self._build_setup_ui()

    # ---------- step 1: name + action ----------

    def _build_setup_ui(self):
        for w in self.winfo_children():
            w.destroy()
        pad = ttk.Frame(self, padding=16)
        pad.grid(row=0, column=0)
        row = 0

        ttk.Label(pad, text="Gesture name:").grid(row=row, column=0, sticky="w"); row += 1
        self.name_var = tk.StringVar()
        ttk.Entry(pad, textvariable=self.name_var, width=36).grid(row=row, column=0, sticky="ew", pady=(0, 10)); row += 1

        ttk.Label(pad, text="Action type:").grid(row=row, column=0, sticky="w"); row += 1
        self.action_type_var = tk.StringVar(value="builtin")
        type_row = ttk.Frame(pad)
        type_row.grid(row=row, column=0, sticky="w", pady=(0, 8)); row += 1
        ttk.Radiobutton(type_row, text="Built-in", variable=self.action_type_var, value="builtin",
                         command=self._show_action_subframe).pack(side="left")
        ttk.Radiobutton(type_row, text="Keyboard shortcut", variable=self.action_type_var, value="keyboard",
                         command=self._show_action_subframe).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(type_row, text="Mouse click", variable=self.action_type_var, value="mouse_click",
                         command=self._show_action_subframe).pack(side="left", padx=(10, 0))

        self._subframe_row = row
        row += 1

        self._builtin_frame = ttk.Frame(pad)
        labels = [label for label, _fn in ACTIONS.values()]
        self.combo = ttk.Combobox(self._builtin_frame, values=labels, state="readonly", width=34)
        self.combo.pack()
        self.combo.current(0)

        self._keyboard_frame = ttk.Frame(pad)
        ttk.Label(self._keyboard_frame, text="Click the box below, then press your key combo:",
                  justify="left").pack(anchor="w")
        self.keyboard_display_var = tk.StringVar(value="(click here and press keys)")
        key_box = tk.Label(self._keyboard_frame, textvariable=self.keyboard_display_var,
                            relief="solid", borderwidth=1, width=34, height=2, takefocus=1)
        key_box.pack(pady=(4, 4))
        key_box.bind("<Button-1>", lambda e: key_box.focus_set())
        self._combo_recorder = ComboRecorder(self._on_combo_captured)
        self._combo_recorder.bind(key_box)
        ttk.Button(self._keyboard_frame, text="Clear", command=self._clear_combo).pack(anchor="w")

        self._mouse_frame = ttk.Frame(pad)
        ttk.Label(self._mouse_frame, text="Click type:").grid(row=0, column=0, sticky="w")
        self.mouse_kind_combo = ttk.Combobox(self._mouse_frame, values=list(_MOUSE_CLICK_KINDS.keys()),
                                              state="readonly", width=20)
        self.mouse_kind_combo.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.mouse_kind_combo.current(0)
        ttk.Label(self._mouse_frame,
                  text="Move your mouse to the target spot (nothing will be\nclicked now), then capture the position:",
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 4))
        ttk.Button(self._mouse_frame, text="Capture Position in 3s",
                   command=self._start_mouse_capture).grid(row=2, column=0, sticky="w")
        self.mouse_status_var = tk.StringVar(value="No position captured yet.")
        ttk.Label(self._mouse_frame, textvariable=self.mouse_status_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self._show_action_subframe()

        ttk.Label(
            pad, justify="left",
            text="Then hold a distinct hand pose (not a fist -- that's\n"
                 "reserved for grabbing windows) steady for ~1.5 seconds.",
        ).grid(row=row + 1, column=0, sticky="w", pady=(10, 10))

        btn_row = ttk.Frame(pad)
        btn_row.grid(row=row + 2, column=0, sticky="ew")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="Next: Record Gesture", command=self._start_recording).pack(side="right")

    def _show_action_subframe(self):
        for f in (self._builtin_frame, self._keyboard_frame, self._mouse_frame):
            f.grid_remove()
        kind = self.action_type_var.get()
        frame = {"builtin": self._builtin_frame, "keyboard": self._keyboard_frame,
                  "mouse_click": self._mouse_frame}[kind]
        frame.grid(row=self._subframe_row, column=0, sticky="w", pady=(0, 4))

    def _on_combo_captured(self, vk_codes, display):
        self._captured_combo = (vk_codes, display)
        self.keyboard_display_var.set(display)

    def _clear_combo(self):
        self._captured_combo = None
        self._combo_recorder.reset()
        self.keyboard_display_var.set("(click here and press keys)")

    def _start_mouse_capture(self):
        if self._mouse_countdown_job:
            self.after_cancel(self._mouse_countdown_job)
        self._mouse_countdown = 3
        self._tick_mouse_countdown()

    def _tick_mouse_countdown(self):
        if self._mouse_countdown > 0:
            self.mouse_status_var.set(f"Capturing in {self._mouse_countdown}...")
            self._mouse_countdown -= 1
            self._mouse_countdown_job = self.after(1000, self._tick_mouse_countdown)
        else:
            x, y = win32api.GetCursorPos()
            self._captured_mouse_pos = (x, y)
            self.mouse_status_var.set(f"Captured position: ({x}, {y})")
            self._mouse_countdown_job = None

    # ---------- step 2: record the trigger pose ----------

    def _start_recording(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Give the gesture a name first.", parent=self)
            return

        action_type = self.action_type_var.get()
        if action_type == "builtin":
            self._chosen_action_type = "builtin"
            self._chosen_action = self._action_keys[self.combo.current()]
            self._chosen_action_params = None
        elif action_type == "keyboard":
            if not self._captured_combo:
                messagebox.showwarning("No shortcut recorded",
                                        "Click the key box and press a combo first.", parent=self)
                return
            vk_codes, display = self._captured_combo
            self._chosen_action_type = "keyboard"
            self._chosen_action = None
            self._chosen_action_params = {"vk_codes": vk_codes, "display": display}
        else:
            if not self._captured_mouse_pos:
                messagebox.showwarning("No position captured",
                                        "Capture a mouse position first.", parent=self)
                return
            button, clicks = _MOUSE_CLICK_KINDS[self.mouse_kind_combo.get()]
            x, y = self._captured_mouse_pos
            self._chosen_action_type = "mouse_click"
            self._chosen_action = None
            self._chosen_action_params = {"x": x, "y": y, "button": button, "clicks": clicks}

        self._chosen_name = name
        self._final_features = None
        self._recording_done = False

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

        if self.recorder and not self.recorder.running and self._recording_done:
            if self._final_features is None:
                messagebox.showinfo("No hand detected", "Didn't see a hand -- try again.", parent=self)
                self._build_setup_ui()
                return
            templates = load_templates()
            key = f"gesture_{int(time.time() * 1000)}"
            templates.append({
                "key": key,
                "name": self._chosen_name,
                "action_type": self._chosen_action_type,
                "action": self._chosen_action,
                "action_params": self._chosen_action_params,
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
        if self._mouse_countdown_job:
            self.after_cancel(self._mouse_countdown_job)
        if self.recorder and self.recorder.running:
            threading.Thread(target=self.recorder.stop, daemon=True).start()
        self.destroy()
