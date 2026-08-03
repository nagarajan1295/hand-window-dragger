"""Calibration tutorial window -- a Face-ID-style guided wizard on top of
calibration.CalibrationSession."""

import queue
import threading
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

from calibration import CalibrationSession

PREVIEW_W, PREVIEW_H = 640, 360


class CalibrationWindow(tk.Toplevel):
    def __init__(self, parent, monitors, camera_index, mirror, on_finished):
        super().__init__(parent)
        self.title("Calibration Tutorial")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.on_finished = on_finished  # callback(calibration_dict_or_None)
        self._result = None

        self.frame_queue = queue.Queue(maxsize=1)
        self.event_queue = queue.Queue()

        self.session = CalibrationSession(
            monitors, starting_mirror=mirror, camera_index=camera_index,
            on_frame=self._on_frame, on_event=self._on_event, on_complete=self._on_complete,
        )

        self._build_ui()
        self.session.start()
        self._poll()

    def _build_ui(self):
        pad = ttk.Frame(self, padding=16)
        pad.grid(row=0, column=0)

        self.progress_var = tk.StringVar(value="Getting ready...")
        ttk.Label(pad, textvariable=self.progress_var, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")

        self.instruction_var = tk.StringVar(value="Position yourself in front of the camera")
        ttk.Label(pad, textvariable=self.instruction_var, font=("Segoe UI", 15, "bold"),
                  wraplength=620, justify="left").grid(row=1, column=0, sticky="w", pady=(4, 10))

        self.preview_label = ttk.Label(pad)
        self.preview_label.grid(row=2, column=0)
        blank = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (30, 30, 30))
        self._blank_img = ImageTk.PhotoImage(blank)
        self.preview_label.configure(image=self._blank_img)

        self.progress_bar = ttk.Progressbar(pad, length=PREVIEW_W, maximum=1.0)
        self.progress_bar.grid(row=3, column=0, sticky="ew", pady=(10, 10))

        btn_row = ttk.Frame(pad)
        btn_row.grid(row=4, column=0, sticky="ew")
        ttk.Button(btn_row, text="Cancel", command=self._on_cancel).pack(side="right")

    def _on_frame(self, frame, info):
        h, w = frame.shape[:2]
        n = len(info["monitors"])
        for i in range(1, n):
            x = int(w * i / n)
            cv2.line(frame, (x, 0), (x, h), (80, 80, 80), 1)
        if info["zone"] is not None and info.get("sub_phase") == "GRABBED":
            i = info["zone"]
            band = frame.copy()
            cv2.rectangle(band, (int(w * i / n), 0), (int(w * (i + 1) / n), h), (0, 200, 200), -1)
            cv2.addWeighted(band, 0.15, frame, 0.85, 0, frame)
        try:
            self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frame_queue.put_nowait((frame, info))
        except queue.Full:
            pass

    def _on_event(self, msg):
        self.event_queue.put(("log", msg))

    def _on_complete(self, calibration):
        # Set the result before enqueueing so the polling loop never reads
        # the completion sentinel ahead of the value it depends on.
        self._result = calibration
        self.event_queue.put(("complete", calibration))

    def _poll(self):
        try:
            while True:
                frame, info = self.frame_queue.get_nowait()
                self._update_preview(frame)
                self.instruction_var.set(info["instruction"])
                step = min(info["step_index"] + 1, info["total_steps"])
                self.progress_var.set(f"Step {step} of {info['total_steps']}")
                self.progress_bar["value"] = info["step_index"] / info["total_steps"]
        except queue.Empty:
            pass

        finished = False
        try:
            while True:
                kind, _payload = self.event_queue.get_nowait()
                if kind == "complete":
                    finished = True
        except queue.Empty:
            pass

        if finished:
            self.progress_var.set("Done")
            self.instruction_var.set("Calibration complete!" if self._result else "Calibration cancelled.")
            self.after(1200, self._close)
            return

        self.after(15, self._poll)

    def _update_preview(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb).resize((PREVIEW_W, PREVIEW_H))
        photo = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=photo)
        self.preview_label.image = photo

    def _on_cancel(self):
        threading.Thread(target=self.session.cancel, daemon=True).start()
        self._result = None
        self._close()

    def _close(self):
        if self.on_finished:
            self.on_finished(self._result)
        self.destroy()
