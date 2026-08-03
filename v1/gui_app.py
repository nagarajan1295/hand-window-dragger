"""Tkinter control panel for the hand-tracking window dragger.

A point-and-click front end around engine.HandDraggerEngine: live camera
preview with the same overlay as the CLI, start/stop, sensitivity
sliders, mirror/maximize toggles, camera picker, and an activity log.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

from config_io import load_config, save_config
from engine import HandDraggerEngine
from overlay import draw_landmarks, draw_overlay

PREVIEW_W, PREVIEW_H = 640, 360


class App:
    def __init__(self, root):
        self.root = root
        root.title("Hand Window Dragger")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cfg = load_config()
        self.frame_queue = queue.Queue(maxsize=1)
        self.event_queue = queue.Queue()

        self.engine = HandDraggerEngine(config=self.cfg, on_frame=self._on_frame, on_event=self._on_event)

        self._build_ui()
        self._poll()

    # ---------- UI construction ----------

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")

        self.preview_label = ttk.Label(main)
        self.preview_label.grid(row=0, column=0, rowspan=30, padx=(0, 12))
        blank = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (30, 30, 30))
        self._blank_img = ImageTk.PhotoImage(blank)
        self.preview_label.configure(image=self._blank_img)

        col = 1
        r = 0

        self.start_btn = ttk.Button(main, text="Start Tracking", command=self.toggle_engine)
        self.start_btn.grid(row=r, column=col, sticky="ew", pady=(0, 8)); r += 1

        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(main, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).grid(row=r, column=col, sticky="w"); r += 1

        self.hold_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.hold_var, foreground="#1a7f37").grid(row=r, column=col, sticky="w", pady=(0, 8)); r += 1

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Monitors (left to right):").grid(row=r, column=col, sticky="w"); r += 1
        for m in self.engine.monitors:
            ttk.Label(main, text=f"  {m['device']}").grid(row=r, column=col, sticky="w"); r += 1

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        self.mirror_var = tk.BooleanVar(value=self.cfg["mirror"])
        ttk.Checkbutton(main, text="Mirror preview", variable=self.mirror_var,
                         command=self._apply_checks).grid(row=r, column=col, sticky="w"); r += 1

        self.maximize_var = tk.BooleanVar(value=self.cfg["maximize_on_drop"])
        ttk.Checkbutton(main, text="Maximize window on drop", variable=self.maximize_var,
                         command=self._apply_checks).grid(row=r, column=col, sticky="w"); r += 1

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Camera index:").grid(row=r, column=col, sticky="w"); r += 1
        self.camera_var = tk.IntVar(value=self.cfg["camera_index"])
        spin = ttk.Spinbox(main, from_=0, to=8, width=5, textvariable=self.camera_var,
                            command=self._apply_camera)
        spin.grid(row=r, column=col, sticky="w"); r += 1
        spin.bind("<Return>", lambda e: self._apply_camera())
        spin.bind("<FocusOut>", lambda e: self._apply_camera())

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        r = self._add_slider(main, r, col, "Fist sensitivity (fingers curled)", "curled_threshold", 1, 4)
        r = self._add_slider(main, r, col, "Grab hold frames", "grab_debounce_frames", 1, 15)
        r = self._add_slider(main, r, col, "Release hold frames", "release_debounce_frames", 1, 15)
        r = self._add_slider(main, r, col, "Lost-hand grace frames", "lost_grace_frames", 5, 60)

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1
        ttk.Button(main, text="Save Settings", command=self._save).grid(row=r, column=col, sticky="ew"); r += 1

        ttk.Label(main, text="Activity log:").grid(row=r, column=col, sticky="w", pady=(8, 0)); r += 1
        self.log_text = tk.Text(main, height=8, width=44, state="disabled", wrap="word")
        self.log_text.grid(row=r, column=col, sticky="ew"); r += 1

    def _add_slider(self, parent, r, col, label_text, key, lo, hi):
        label = ttk.Label(parent, text=f"{label_text}: {self.cfg[key]}")
        label.grid(row=r, column=col, sticky="w")
        r += 1
        scale = ttk.Scale(
            parent, from_=lo, to=hi, orient="horizontal",
            command=lambda v, k=key, lbl=label, txt=label_text: self._on_slider(k, v, lbl, txt),
        )
        scale.set(self.cfg[key])
        scale.grid(row=r, column=col, sticky="ew")
        r += 1
        return r

    # ---------- control callbacks (main thread) ----------

    def _on_slider(self, key, value, label_widget, label_text):
        ivalue = int(round(float(value)))
        self.cfg[key] = ivalue
        label_widget.configure(text=f"{label_text}: {ivalue}")

    def _apply_checks(self):
        self.cfg["mirror"] = self.mirror_var.get()
        self.cfg["maximize_on_drop"] = self.maximize_var.get()

    def _apply_camera(self):
        self.cfg["camera_index"] = self.camera_var.get()

    def toggle_engine(self):
        if self.engine.running:
            threading.Thread(target=self.engine.stop, daemon=True).start()
        else:
            self.engine.start()

    def _save(self):
        save_config(self.cfg)
        self._append_log("Settings saved.")

    def on_close(self):
        if self.engine.running:
            self.engine.stop()
        save_config(self.cfg)
        self.root.destroy()

    # ---------- engine callbacks (background thread -- queue only) ----------

    def _on_frame(self, frame, info):
        if info["landmarks"] is not None:
            draw_landmarks(frame, info["landmarks"])
        draw_overlay(
            frame, info["monitors"], info["zone"], info["state"],
            info["grabbed_title"], info["fist_active"], info["hand_present"],
        )
        try:
            self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frame_queue.put_nowait((frame, info["state"], info["grabbed_title"]))
        except queue.Full:
            pass

    def _on_event(self, msg):
        self.event_queue.put(msg)

    # ---------- main-thread polling ----------

    def _poll(self):
        if self.engine.running and self.start_btn["text"] != "Stop Tracking":
            self.start_btn.configure(text="Stop Tracking")
        elif not self.engine.running and self.start_btn["text"] != "Start Tracking":
            self.start_btn.configure(text="Start Tracking")
            self.status_var.set("Stopped")
            self.hold_var.set("")

        try:
            while True:
                frame, state, grabbed_title = self.frame_queue.get_nowait()
                self._update_preview(frame)
                self.status_var.set(state)
                self.hold_var.set(f"Holding: {grabbed_title}" if grabbed_title else "")
        except queue.Empty:
            pass

        try:
            while True:
                msg = self.event_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass

        self.root.after(15, self._poll)

    def _update_preview(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb).resize((PREVIEW_W, PREVIEW_H))
        photo = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=photo)
        self.preview_label.image = photo  # keep a reference or Tk garbage-collects it

    def _append_log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
