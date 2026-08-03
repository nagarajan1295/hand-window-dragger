"""Face-enrollment wizard window -- same visual pattern as the calibration
tutorial: live preview, big instruction text, a progress bar per prompt."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import cv2
from PIL import Image, ImageTk

from face_auth import FaceEnrollmentSession

PREVIEW_W, PREVIEW_H = 480, 270


def ask_person_name(parent):
    return simpledialog.askstring("Enroll a face", "Name for this person:", parent=parent)


class FaceEnrollWindow(tk.Toplevel):
    def __init__(self, parent, name, camera_index, mirror, on_finished):
        super().__init__(parent)
        self.title(f"Enrolling {name}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.on_finished = on_finished  # callback(label_or_None)
        self._result = None

        self.frame_queue = queue.Queue(maxsize=1)
        self.event_queue = queue.Queue()

        self.session = FaceEnrollmentSession(
            name, camera_index=camera_index, mirror=mirror,
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

        self.instruction_var = tk.StringVar(value="Position your face in front of the camera")
        ttk.Label(pad, textvariable=self.instruction_var, font=("Segoe UI", 14, "bold"),
                  wraplength=460, justify="left").grid(row=1, column=0, sticky="w", pady=(4, 10))

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

    def _on_complete(self, label):
        self._result = label
        self.event_queue.put(("complete", label))

    def _poll(self):
        try:
            while True:
                frame, info = self.frame_queue.get_nowait()
                self._update_preview(frame)
                self.instruction_var.set(info["instruction"])
                step = info["prompt_index"] + 1
                self.progress_var.set(f"Pose {step} of {info['total_prompts']}"
                                       + ("" if info["face_detected"] else "  (no face detected)"))
                self.progress_bar["value"] = info["progress"]
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
            self.instruction_var.set("Enrollment complete!" if self._result is not None else "Enrollment cancelled.")
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
