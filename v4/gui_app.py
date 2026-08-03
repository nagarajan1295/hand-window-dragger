"""Tkinter control panel for the hand-tracking window dragger.

A point-and-click front end around engine.HandDraggerEngine: live camera
preview with the same overlay as the CLI, start/stop, a guided calibration
tutorial, sensitivity sliders, custom gesture management, face-recognition
security, and an activity log.
"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import win32gui
from PIL import Image, ImageTk

from actions import action_label
from calibration import load_calibration
from calibration_ui import CalibrationWindow
from config_io import load_config, save_config
from drag_overlay import DragOverlay, capture_window_thumbnail, make_crumpled_icon
from engine import HandDraggerEngine
from face_auth import load_labels, remove_person
from face_ui import FaceEnrollWindow, ask_person_name
from gesture_templates import load_templates, save_templates
from gesture_ui import AddGestureDialog
from overlay import draw_landmarks, draw_overlay
from window_manager import virtual_desktop_bounds

BALL_SIZE = 120

PREVIEW_W, PREVIEW_H = 640, 360


class App:
    def __init__(self, root):
        self.root = root
        root.title("Hand Window Dragger")
        root.resizable(True, True)
        root.geometry("1040x760")
        root.minsize(760, 480)
        root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.cfg = load_config()
        self.frame_queue = queue.Queue(maxsize=1)
        self.event_queue = queue.Queue()
        self._modal_open = False  # calibration / gesture / face dialog owns the camera

        self._prev_drag_state = "IDLE"
        self._drag_overlay = None
        self._drag_hwnd = None
        self._drag_source_rect = None
        self._drag_last_ball_rect = None

        self.engine = HandDraggerEngine(config=self.cfg, on_frame=self._on_frame, on_event=self._on_event)

        self._build_ui()
        self._refresh_calibration_banner()
        self._refresh_gesture_list()
        self._refresh_face_list()
        self._poll()

    # ---------- UI construction ----------

    def _build_ui(self):
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        container = ttk.Frame(self.root, padding=10)
        container.grid(row=0, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        self.preview_label = ttk.Label(container)
        self.preview_label.grid(row=0, column=0, sticky="n", padx=(0, 12))
        blank = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (30, 30, 30))
        self._blank_img = ImageTk.PhotoImage(blank)
        self.preview_label.configure(image=self._blank_img)

        # Settings live in a scrollable pane so the window can be shorter
        # than all the controls stacked up (the Quit button, calibration,
        # gestures, and face list add up to more than fits on many
        # screens) without anything becoming unreachable.
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=1, sticky="nsew")
        scrollbar.grid(row=0, column=2, sticky="ns")

        main = ttk.Frame(canvas)
        settings_window = canvas.create_window((0, 0), window=main, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        main.bind("<Configure>", _sync_scrollregion)

        def _sync_width(event):
            canvas.itemconfigure(settings_window, width=event.width)
        canvas.bind("<Configure>", _sync_width)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        col = 0
        r = 0

        self.calib_banner_var = tk.StringVar(value="")
        self.calib_banner = ttk.Label(main, textvariable=self.calib_banner_var, foreground="#b35c00",
                                       wraplength=280, justify="left")
        self.calib_banner.grid(row=r, column=col, sticky="w", pady=(0, 6)); r += 1

        self.start_btn = ttk.Button(main, text="Start Tracking", command=self.toggle_engine)
        self.start_btn.grid(row=r, column=col, sticky="ew", pady=(0, 4)); r += 1

        self.calibrate_btn = ttk.Button(main, text="Run Calibration Tutorial", command=self.run_calibration)
        self.calibrate_btn.grid(row=r, column=col, sticky="ew", pady=(0, 8)); r += 1

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

        self.zone_grab_var = tk.BooleanVar(value=self.cfg["zone_based_grab"])
        ttk.Checkbutton(main, text="Grab whatever window is on your hand's monitor\n"
                                    "(instead of whatever app you last clicked)",
                         variable=self.zone_grab_var, command=self._apply_checks).grid(
            row=r, column=col, sticky="w"); r += 1

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Drag animation:").grid(row=r, column=col, sticky="w"); r += 1
        self.animation_var = tk.StringVar(value=self.cfg["animation_mode"])
        for val, label in (("none", "None (default)"), ("swish", "Glow + swish"), ("paper", "Crushed paper")):
            ttk.Radiobutton(main, text=label, variable=self.animation_var, value=val,
                             command=self._apply_checks).grid(row=r, column=col, sticky="w")
            r += 1

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

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Custom gestures:").grid(row=r, column=col, sticky="w"); r += 1
        self.gesture_list = tk.Listbox(main, height=5, width=44)
        self.gesture_list.grid(row=r, column=col, sticky="ew"); r += 1
        gbtn_row = ttk.Frame(main)
        gbtn_row.grid(row=r, column=col, sticky="ew", pady=(4, 0)); r += 1
        ttk.Button(gbtn_row, text="Add New...", command=self.add_gesture).pack(side="left")
        ttk.Button(gbtn_row, text="Delete Selected", command=self.delete_gesture).pack(side="left", padx=(6, 0))

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Face security:").grid(row=r, column=col, sticky="w"); r += 1
        self.face_lock_var = tk.BooleanVar(value=self.cfg["face_lock_enabled"])
        ttk.Checkbutton(main, text="Only respond to gestures from an enrolled face",
                         variable=self.face_lock_var, command=self._apply_checks).grid(
            row=r, column=col, sticky="w"); r += 1
        self.face_list = tk.Listbox(main, height=3, width=44)
        self.face_list.grid(row=r, column=col, sticky="ew"); r += 1
        fbtn_row = ttk.Frame(main)
        fbtn_row.grid(row=r, column=col, sticky="ew", pady=(4, 0)); r += 1
        ttk.Button(fbtn_row, text="Enroll New Person...", command=self.enroll_face).pack(side="left")
        ttk.Button(fbtn_row, text="Remove Selected", command=self.remove_face).pack(side="left", padx=(6, 0))

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Activity log:").grid(row=r, column=col, sticky="w", pady=(2, 0)); r += 1
        self.log_text = tk.Text(main, height=7, width=44, state="disabled", wrap="word")
        self.log_text.grid(row=r, column=col, sticky="ew"); r += 1

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1
        ttk.Button(main, text="Quit", command=self.quit_app).grid(row=r, column=col, sticky="ew"); r += 1

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
        self.cfg["zone_based_grab"] = self.zone_grab_var.get()
        self.cfg["animation_mode"] = self.animation_var.get()
        if self.face_lock_var.get() and not load_labels():
            messagebox.showwarning(
                "No enrolled faces",
                "Enroll at least one person before turning on face security.",
                parent=self.root,
            )
            self.face_lock_var.set(False)
        self.cfg["face_lock_enabled"] = self.face_lock_var.get()

    def _apply_camera(self):
        self.cfg["camera_index"] = self.camera_var.get()

    def toggle_engine(self):
        if self._modal_open:
            return
        if self.engine.running:
            threading.Thread(target=self.engine.stop, daemon=True).start()
        else:
            self.engine.start()

    def _save(self):
        save_config(self.cfg)
        self._append_log("Settings saved.")

    def quit_app(self):
        if self.engine.running:
            self.engine.stop()
        if self._drag_overlay:
            self._drag_overlay.destroy()
            self._drag_overlay = None
        save_config(self.cfg)
        self.root.destroy()

    def _rebuild_ui(self):
        for w in list(self.root.grid_slaves()):
            w.destroy()
        self._build_ui()
        self._refresh_calibration_banner()
        self._refresh_gesture_list()
        self._refresh_face_list()

    # ---------- calibration ----------

    def run_calibration(self):
        if self._modal_open or self.engine.running:
            messagebox.showinfo("Busy", "Stop tracking before running calibration.", parent=self.root)
            return
        self._modal_open = True
        self.start_btn.configure(state="disabled")
        self.calibrate_btn.configure(state="disabled")
        CalibrationWindow(
            self.root, self.engine.monitors, self.cfg["camera_index"], self.cfg["mirror"],
            on_finished=self._on_calibration_finished,
        )

    def _on_calibration_finished(self, calibration):
        self._modal_open = False
        if calibration:
            self.cfg["mirror"] = calibration["mirror"]
            self.cfg["curled_threshold"] = calibration["curled_threshold"]
            save_config(self.cfg)
            self._append_log(
                f"Calibration saved: mirror={calibration['mirror']}, "
                f"fist sensitivity={calibration['curled_threshold']}."
            )
        else:
            self._append_log("Calibration cancelled.")
        # Sliders/checkboxes don't auto-refresh from cfg; rebuild the panel.
        self._rebuild_ui()

    def _refresh_calibration_banner(self):
        calib = load_calibration()
        if calib:
            self.calib_banner_var.set("")
        else:
            self.calib_banner_var.set(
                "Not calibrated yet -- run the tutorial so the app learns your "
                "hand and monitor layout."
            )

    # ---------- custom gestures ----------

    def add_gesture(self):
        if self._modal_open or self.engine.running:
            messagebox.showinfo("Busy", "Stop tracking before adding a gesture.", parent=self.root)
            return
        self._modal_open = True
        self.start_btn.configure(state="disabled")
        self.calibrate_btn.configure(state="disabled")
        dialog = AddGestureDialog(self.root, self.cfg["camera_index"], self.cfg["mirror"],
                                   on_saved=self._on_gesture_saved)
        self.root.wait_window(dialog)
        self._modal_open = False
        self.start_btn.configure(state="normal")
        self.calibrate_btn.configure(state="normal")

    def _on_gesture_saved(self):
        self._refresh_gesture_list()
        self.engine.reload_templates()
        self._append_log("Custom gesture saved.")

    def delete_gesture(self):
        sel = self.gesture_list.curselection()
        if not sel:
            return
        templates = load_templates()
        idx = sel[0]
        if idx >= len(templates):
            return
        removed = templates.pop(idx)
        save_templates(templates)
        self.engine.reload_templates()
        self._refresh_gesture_list()
        self._append_log(f"Deleted gesture '{removed['name']}'.")

    def _refresh_gesture_list(self):
        self.gesture_list.delete(0, "end")
        for t in load_templates():
            self.gesture_list.insert("end", f"{t['name']}  ->  {action_label(t)}")

    # ---------- face security ----------

    def enroll_face(self):
        if self._modal_open or self.engine.running:
            messagebox.showinfo("Busy", "Stop tracking before enrolling a face.", parent=self.root)
            return
        name = ask_person_name(self.root)
        if not name:
            return
        self._modal_open = True
        self.start_btn.configure(state="disabled")
        self.calibrate_btn.configure(state="disabled")
        dialog = FaceEnrollWindow(self.root, name, self.cfg["camera_index"], self.cfg["mirror"],
                                   on_finished=self._on_face_enrolled)
        self.root.wait_window(dialog)
        self._modal_open = False
        self.start_btn.configure(state="normal")
        self.calibrate_btn.configure(state="normal")

    def _on_face_enrolled(self, label):
        self._refresh_face_list()
        self.engine.reload_face_gate()
        if label is not None:
            self._append_log("Face enrolled.")
        else:
            self._append_log("Face enrollment cancelled.")

    def remove_face(self):
        sel = self.face_list.curselection()
        if not sel:
            return
        labels = load_labels()
        items = sorted(labels.items())
        idx = sel[0]
        if idx >= len(items):
            return
        person_label, name = items[idx]
        remove_person(person_label)
        self.engine.reload_face_gate()
        self._refresh_face_list()
        self._append_log(f"Removed enrolled face '{name}'.")
        # Deliberately do NOT auto-disable face_lock_enabled here even if
        # that was the last enrolled person: silently falling back to "let
        # anyone through" would make a deletion look like it didn't take
        # effect. With zero enrolled faces left, the engine locks out
        # everyone (face_authorized never goes True) until a new face is
        # enrolled -- that's the correct, safe state.
        if not load_labels() and self.cfg["face_lock_enabled"]:
            self._append_log("No enrolled faces left -- gestures are locked until you enroll one.")

    def _refresh_face_list(self):
        self.face_list.delete(0, "end")
        for _label, name in sorted(load_labels().items()):
            self.face_list.insert("end", name)

    # ---------- engine callbacks (background thread -- queue only) ----------

    def _on_frame(self, frame, info):
        if info["landmarks"] is not None:
            draw_landmarks(frame, info["landmarks"])
        draw_overlay(
            frame, info["monitors"], info["zone"], info["state"],
            info["grabbed_title"], info["fist_active"], info["hand_present"],
            info["active_gesture_name"], info["gesture_hold"], info["gesture_hold_needed"],
            info["face_lock_enabled"], info["face_authorized"], info["face_name"],
        )
        try:
            self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frame_queue.put_nowait((frame, info))
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
                frame, info = self.frame_queue.get_nowait()
                self._update_preview(frame)
                self.status_var.set(info["state"])
                self.hold_var.set(f"Holding: {info['grabbed_title']}" if info["grabbed_title"] else "")
                self._update_drag_overlay(info)
        except queue.Empty:
            pass

        try:
            while True:
                msg = self.event_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass

        self.root.after(15, self._poll)

    def _update_drag_overlay(self, info):
        mode = self.cfg["animation_mode"]
        state = info["state"]
        prev = self._prev_drag_state

        if mode == "none":
            if self._drag_overlay:
                self._drag_overlay.destroy()
                self._drag_overlay = None
            self._prev_drag_state = state
            return

        if prev == "IDLE" and state == "HOLDING":
            self._start_drag_overlay(mode, info)
        elif state == "HOLDING" and mode == "paper":
            self._move_paper_ball(info)
        elif prev == "HOLDING" and state == "IDLE":
            self._finish_drag_overlay(mode)

        self._prev_drag_state = state

    def _start_drag_overlay(self, mode, info):
        hwnd = info["grabbed_hwnd"]
        self._drag_hwnd = hwnd
        self._drag_source_rect = None
        if self._drag_overlay:
            self._drag_overlay.destroy()
        vb = virtual_desktop_bounds(info["monitors"])
        margin = 60
        bounds = (vb[0] - margin, vb[1] - margin, vb[2] + margin, vb[3] + margin)
        self._drag_overlay = DragOverlay(self.root, bounds)

        if not hwnd or not win32gui.IsWindow(hwnd):
            return
        self._drag_source_rect = win32gui.GetWindowRect(hwnd)

        if mode == "swish":
            self._drag_overlay.show_glow(self._drag_source_rect, color="#00e5ff")
        elif mode == "paper":
            img = capture_window_thumbnail(hwnd)
            if img:
                crumpled = make_crumpled_icon(img, size=BALL_SIZE)
                l, t, r, b = self._drag_source_rect
                cx, cy = (l + r) / 2, (t + b) / 2
                self._drag_overlay.show_ball(crumpled, cx, cy)
                self._drag_last_ball_rect = (cx - BALL_SIZE / 2, cy - BALL_SIZE / 2,
                                              cx + BALL_SIZE / 2, cy + BALL_SIZE / 2)

    def _move_paper_ball(self, info):
        if not self._drag_overlay or info["hand_x"] is None:
            return
        monitors = info["monitors"]
        vb = virtual_desktop_bounds(monitors)
        x = vb[0] + info["hand_x"] * (vb[2] - vb[0])
        zone = info["zone"]
        if zone is not None and info["hand_y"] is not None:
            mt, mb = monitors[zone]["monitor"][1], monitors[zone]["monitor"][3]
            y = mt + info["hand_y"] * (mb - mt)
        else:
            y = (vb[1] + vb[3]) / 2
        self._drag_overlay.move_ball(x, y)
        self._drag_last_ball_rect = (x - BALL_SIZE / 2, y - BALL_SIZE / 2, x + BALL_SIZE / 2, y + BALL_SIZE / 2)

    def _finish_drag_overlay(self, mode=None):
        overlay = self._drag_overlay
        hwnd = self._drag_hwnd
        self._drag_hwnd = None
        if not overlay:
            return
        if not hwnd or not win32gui.IsWindow(hwnd):
            overlay.destroy()
            self._drag_overlay = None
            return

        # The engine has already moved the real window instantly by the
        # time this fires; the target rect is just wherever it landed.
        target_rect = win32gui.GetWindowRect(hwnd)
        if mode == "paper" and self._drag_last_ball_rect:
            start_rect = self._drag_last_ball_rect
            color = "#d8c48a"
        else:
            start_rect = self._drag_source_rect or target_rect
            color = "#00e5ff"

        def _teardown():
            overlay.destroy()
            if self._drag_overlay is overlay:
                self._drag_overlay = None

        overlay.animate_swish(start_rect, target_rect, color=color, on_done=_teardown)
        self._drag_source_rect = None
        self._drag_last_ball_rect = None

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
