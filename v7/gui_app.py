"""Tkinter control panel for the hand-tracking window dragger.

A point-and-click front end around engine.HandDraggerEngine: live camera
preview with the same overlay as the CLI, start/stop, a guided calibration
tutorial, sensitivity sliders, custom gesture management, face-recognition
security, and an activity log.
"""

import os
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
from greeting import JarvisGreeting
from greeting_ui import GreetingSettingsDialog
from overlay import draw_landmarks, draw_overlay
from pattern_learning import generate_report
from window_manager import get_topmost_window_on_monitor, virtual_desktop_bounds

BALL_SIZE = 120
PORTAL_SIZE = 132
BALL_SMOOTHING_ALPHA = 0.35  # higher = snappier/more jittery, lower = smoother/more lag

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
        self._ball_smooth_pos = None
        self._last_highlight_zone = None
        self._highlight_hwnd = None
        self._blackout_win = None

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

        ttk.Label(main, text="Follow visual (while dragging):").grid(row=r, column=col, sticky="w"); r += 1
        self.follow_style_var = tk.StringVar(value=self.cfg["follow_style"])
        for val, label in (("none", "None (default)"), ("paper", "Crushed paper"), ("portal", "Portal")):
            ttk.Radiobutton(main, text=label, variable=self.follow_style_var, value=val,
                             command=self._apply_checks).grid(row=r, column=col, sticky="w")
            r += 1

        ttk.Label(main, text="Other animation layers (combine any):").grid(row=r, column=col, sticky="w", pady=(6, 0)); r += 1
        self.highlight_target_var = tk.BooleanVar(value=self.cfg["highlight_target_enabled"])
        ttk.Checkbutton(main, text="Highlight the window I'm pointing at", variable=self.highlight_target_var,
                         command=self._apply_checks).grid(row=r, column=col, sticky="w"); r += 1
        self.name_label_var = tk.BooleanVar(value=self.cfg["show_name_label"])
        ttk.Checkbutton(main, text="Show window name while dragging", variable=self.name_label_var,
                         command=self._apply_checks).grid(row=r, column=col, sticky="w"); r += 1
        self.particle_trail_var = tk.BooleanVar(value=self.cfg["particle_trail_enabled"])
        ttk.Checkbutton(main, text="Particle trail", variable=self.particle_trail_var,
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

        ttk.Label(main, text="Presence (needs an enrolled face):").grid(row=r, column=col, sticky="w"); r += 1
        self.presence_display_var = tk.BooleanVar(value=self.cfg["presence_display_control_enabled"])
        ttk.Checkbutton(main, text="Turn display off when I leave, on when I return",
                         variable=self.presence_display_var, command=self._apply_checks).grid(
            row=r, column=col, sticky="w"); r += 1
        self.presence_greeting_var = tk.BooleanVar(value=self.cfg["presence_greeting_enabled"])
        ttk.Checkbutton(main, text="Show a greeting when the display turns back on",
                         variable=self.presence_greeting_var, command=self._apply_checks).grid(
            row=r, column=col, sticky="w"); r += 1
        ttk.Button(main, text="Customize Greeting...", command=self.open_greeting_settings).grid(
            row=r, column=col, sticky="ew", pady=(0, 4)); r += 1
        r = self._add_slider(main, r, col, "Away threshold (seconds)", "presence_absence_seconds", 5, 120)

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Gesture safety:").grid(row=r, column=col, sticky="w"); r += 1
        self.require_face_var = tk.BooleanVar(value=self.cfg["require_face_for_gestures"])
        ttk.Checkbutton(main, text="Require a face to be detected for any gesture",
                         variable=self.require_face_var, command=self._apply_checks).grid(
            row=r, column=col, sticky="w"); r += 1
        self.suppress_near_face_var = tk.BooleanVar(value=self.cfg["suppress_hand_near_face"])
        ttk.Checkbutton(main, text="Ignore hand gestures near my face (scratching, etc.)",
                         variable=self.suppress_near_face_var, command=self._apply_checks).grid(
            row=r, column=col, sticky="w"); r += 1

        ttk.Separator(main, orient="horizontal").grid(row=r, column=col, sticky="ew", pady=6); r += 1

        ttk.Label(main, text="Pattern learning:").grid(row=r, column=col, sticky="w"); r += 1
        self.pattern_learning_var = tk.BooleanVar(value=self.cfg["pattern_learning_enabled"])
        ttk.Checkbutton(main, text="Learn my drag/drop patterns", variable=self.pattern_learning_var,
                         command=self._apply_checks).grid(row=r, column=col, sticky="w"); r += 1
        ttk.Button(main, text="Generate Usage Report", command=self.generate_usage_report).grid(
            row=r, column=col, sticky="ew"); r += 1

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
        self.cfg["follow_style"] = self.follow_style_var.get()
        self.cfg["highlight_target_enabled"] = self.highlight_target_var.get()
        self.cfg["show_name_label"] = self.name_label_var.get()
        self.cfg["particle_trail_enabled"] = self.particle_trail_var.get()
        if self.face_lock_var.get() and not load_labels():
            messagebox.showwarning(
                "No enrolled faces",
                "Enroll at least one person before turning on face security.",
                parent=self.root,
            )
            self.face_lock_var.set(False)
        self.cfg["face_lock_enabled"] = self.face_lock_var.get()

        # Presence display control relies entirely on RECOGNIZING an
        # enrolled face to know when to turn the display back on -- with
        # no enrolled faces it would turn the display off the first time
        # you step away and never find a reason to turn it back on.
        if self.presence_display_var.get() and not load_labels():
            messagebox.showwarning(
                "No enrolled faces",
                "Enroll at least one person before turning on presence display control -- "
                "otherwise the display would never know to turn back on.",
                parent=self.root,
            )
            self.presence_display_var.set(False)
        self.cfg["presence_display_control_enabled"] = self.presence_display_var.get()
        self.cfg["presence_greeting_enabled"] = self.presence_greeting_var.get()
        self.cfg["require_face_for_gestures"] = self.require_face_var.get()
        self.cfg["suppress_hand_near_face"] = self.suppress_near_face_var.get()
        self.cfg["pattern_learning_enabled"] = self.pattern_learning_var.get()

    def generate_usage_report(self):
        path = generate_report(self.engine.monitors)
        self._append_log(f"Usage report saved to {path}")
        try:
            os.startfile(path)
        except OSError as e:
            self._append_log(f"Could not open report automatically: {e}")

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
        self._hide_blackout()
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
            self.engine.reload_zone_profile()
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
            self._hide_blackout()  # never leave the screen stuck black after tracking stops

        try:
            while True:
                frame, info = self.frame_queue.get_nowait()
                self._update_preview(frame)
                self.status_var.set(info["state"])
                self.hold_var.set(f"Holding: {info['grabbed_title']}" if info["grabbed_title"] else "")
                self._update_drag_overlay(info)
                if info.get("display_off") and self._blackout_win is None:
                    self._show_blackout()
                elif not info.get("display_off") and self._blackout_win is not None:
                    self._hide_blackout()
                if info.get("greeting_pending") and info.get("greeting_name"):
                    self._show_greeting(info["greeting_name"])
        except queue.Empty:
            pass

        try:
            while True:
                msg = self.event_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass

        self.root.after(15, self._poll)

    def _show_greeting(self, name):
        vb = virtual_desktop_bounds(self.engine.monitors)
        cx, cy = (vb[0] + vb[2]) / 2, (vb[1] + vb[3]) / 2
        JarvisGreeting(self.root, cx, cy, name, self.cfg)

    def open_greeting_settings(self):
        if self._modal_open:
            return
        self._modal_open = True
        dialog = GreetingSettingsDialog(self.root, self.cfg, on_saved=self._on_greeting_settings_saved)
        self.root.wait_window(dialog)
        self._modal_open = False

    def _on_greeting_settings_saved(self):
        save_config(self.cfg)
        self._append_log("Greeting settings saved.")

    def _show_blackout(self):
        """Cover every monitor with an ordinary opaque black window to
        simulate "display off" -- deliberately NOT a real
        WM_SYSCOMMAND/SC_MONITORPOWER call. Actually powering off the
        physical monitor is itself what makes Windows apply its
        "require sign-in" policy the instant display power changes,
        regardless of any idle-timer suppression, which is what was
        locking the session out from under this feature. A plain black
        window never touches real monitor power state, so that policy
        never fires."""
        if self._blackout_win is not None:
            return
        vb = virtual_desktop_bounds(self.engine.monitors)
        x, y, x2, y2 = vb
        w, h = max(1, int(x2 - x)), max(1, int(y2 - y))
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="black")
        win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self._blackout_win = win

    def _hide_blackout(self):
        if self._blackout_win is not None:
            try:
                self._blackout_win.destroy()
            except tk.TclError:
                pass
            self._blackout_win = None

    def _any_animation_active(self):
        return (self.cfg["follow_style"] != "none" or self.cfg["highlight_target_enabled"]
                or self.cfg["show_name_label"] or self.cfg["particle_trail_enabled"])

    def _update_drag_overlay(self, info):
        state = info["state"]
        prev = self._prev_drag_state

        if not self._any_animation_active():
            if self._drag_overlay:
                self._drag_overlay.destroy()
                self._drag_overlay = None
            self._prev_drag_state = state
            return

        if prev == "IDLE" and state == "HOLDING":
            self._start_drag_overlay(info)
        elif state == "HOLDING":
            self._holding_drag_overlay(info)
        elif prev == "HOLDING" and state == "IDLE":
            self._finish_drag_overlay()

        self._prev_drag_state = state

    def _start_drag_overlay(self, info):
        hwnd = info["grabbed_hwnd"]
        self._drag_hwnd = hwnd
        self._drag_source_rect = None
        self._last_highlight_zone = None
        self._highlight_hwnd = None
        if self._drag_overlay:
            self._drag_overlay.destroy()
        vb = virtual_desktop_bounds(info["monitors"])
        margin = 60
        bounds = (vb[0] - margin, vb[1] - margin, vb[2] + margin, vb[3] + margin)
        self._drag_overlay = DragOverlay(self.root, bounds)

        if not hwnd or not win32gui.IsWindow(hwnd):
            return
        self._drag_source_rect = win32gui.GetWindowRect(hwnd)
        l, t, r, b = self._drag_source_rect
        cx, cy = (l + r) / 2, (t + b) / 2
        self._ball_smooth_pos = (cx, cy)  # start the follow motion from the window's own spot

        style = self.cfg["follow_style"]
        if style == "paper":
            img = capture_window_thumbnail(hwnd)
            if img:
                crumpled = make_crumpled_icon(img, size=BALL_SIZE)
                self._drag_overlay.show_ball(crumpled, cx, cy)
                self._drag_last_ball_rect = (cx - BALL_SIZE / 2, cy - BALL_SIZE / 2,
                                              cx + BALL_SIZE / 2, cy + BALL_SIZE / 2)
        elif style == "portal":
            self._drag_overlay.show_portal(cx, cy, size=PORTAL_SIZE)
            half = PORTAL_SIZE / 2
            self._drag_last_ball_rect = (cx - half, cy - half, cx + half, cy + half)
        else:
            self._drag_last_ball_rect = self._drag_source_rect

        if self.cfg["show_name_label"]:
            self._drag_overlay.show_label(info["grabbed_title"] or "", cx, cy)
        if self.cfg["highlight_target_enabled"]:
            self._update_target_highlight(info)

    @staticmethod
    def _calibrated_screen_x(hand_x, monitors, zone_centers):
        """Map hand_x through the SAME learned per-zone centers the engine
        uses for grab targeting (piecewise-linear between them), so the
        follow-visual reaches a monitor exactly when the hand position
        that monitor's zone was actually calibrated to is reached --
        instead of a generic linear map across the whole desktop width
        that can disagree with where zones actually are."""
        mon_x = [(m["monitor"][0] + m["monitor"][2]) / 2 for m in monitors]
        if hand_x <= zone_centers[0]:
            return mon_x[0]
        if hand_x >= zone_centers[-1]:
            return mon_x[-1]
        for i in range(len(zone_centers) - 1):
            c0, c1 = zone_centers[i], zone_centers[i + 1]
            if c0 <= hand_x <= c1:
                t = (hand_x - c0) / (c1 - c0) if c1 != c0 else 0.0
                return mon_x[i] + (mon_x[i + 1] - mon_x[i]) * t
        return mon_x[-1]

    def _holding_drag_overlay(self, info):
        if not self._drag_overlay:
            return
        style = self.cfg["follow_style"]
        if style != "none" and info["hand_x"] is not None:
            self._move_follow_visual(info, style)
        if self.cfg["highlight_target_enabled"]:
            self._update_target_highlight(info)
        else:
            self._drag_overlay.hide_hud_highlight()
            self._last_highlight_zone = None

    def _move_follow_visual(self, info, style):
        monitors = info["monitors"]
        zone_centers = info["zone_centers"]
        target_x = self._calibrated_screen_x(info["hand_x"], monitors, zone_centers)
        zone = info["zone"]
        if zone is not None and info["hand_y"] is not None:
            mt, mb = monitors[zone]["monitor"][1], monitors[zone]["monitor"][3]
            target_y = mt + info["hand_y"] * (mb - mt)
        else:
            vb = virtual_desktop_bounds(monitors)
            target_y = (vb[1] + vb[3]) / 2

        # Exponential smoothing so frame-to-frame landmark jitter doesn't
        # make the follow-visual twitch -- moves a fraction of the
        # remaining distance to the target each frame rather than
        # snapping to it, cursor-like.
        if self._ball_smooth_pos is None:
            self._ball_smooth_pos = (target_x, target_y)
        sx, sy = self._ball_smooth_pos
        sx += (target_x - sx) * BALL_SMOOTHING_ALPHA
        sy += (target_y - sy) * BALL_SMOOTHING_ALPHA
        self._ball_smooth_pos = (sx, sy)

        if style == "paper":
            self._drag_overlay.move_ball(sx, sy)
            half = BALL_SIZE / 2
            self._drag_last_ball_rect = (sx - half, sy - half, sx + half, sy + half)
        elif style == "portal":
            self._drag_overlay.move_portal(sx, sy)
            half = PORTAL_SIZE / 2
            self._drag_last_ball_rect = (sx - half, sy - half, sx + half, sy + half)

        if self.cfg["show_name_label"]:
            self._drag_overlay.show_label(info["grabbed_title"] or "", sx, sy)
        if self.cfg["particle_trail_enabled"]:
            trail_color = {"paper": "#d8c48a", "portal": "#ffd24d"}.get(style, "#cc785c")
            self._drag_overlay.update_trail(sx, sy, color=trail_color)

    def _update_target_highlight(self, info):
        """Corner-bracket highlight around whichever window is topmost on
        the monitor the hand currently points at -- a live preview of
        what a drop right now would land on top of. The (relatively
        expensive) EnumWindows lookup only reruns when the zone actually
        changes; the highlighted window's rect is refreshed every frame
        in case it moved."""
        zone = info["zone"]
        if zone is None:
            return
        if zone != self._last_highlight_zone:
            self._last_highlight_zone = zone
            monitor = info["monitors"][zone]
            self._highlight_hwnd = get_topmost_window_on_monitor(monitor, exclude_pid=os.getpid())
        if self._highlight_hwnd and win32gui.IsWindow(self._highlight_hwnd):
            rect = win32gui.GetWindowRect(self._highlight_hwnd)
            self._drag_overlay.show_hud_highlight(rect)  # uses drag_overlay's THEME_COLOR default
        else:
            self._drag_overlay.hide_hud_highlight()

    def _finish_drag_overlay(self):
        overlay = self._drag_overlay
        hwnd = self._drag_hwnd
        self._drag_hwnd = None
        self._last_highlight_zone = None
        if not overlay:
            return
        overlay.hide_hud_highlight()
        overlay.hide_label()

        if not hwnd or not win32gui.IsWindow(hwnd):
            overlay.destroy()
            self._drag_overlay = None
            self._drag_source_rect = None
            self._drag_last_ball_rect = None
            self._ball_smooth_pos = None
            return

        # The engine has already moved the real window instantly by the
        # time this fires; the target rect is just wherever it landed.
        target_rect = win32gui.GetWindowRect(hwnd)
        style = self.cfg["follow_style"]

        def _teardown():
            overlay.destroy()
            if self._drag_overlay is overlay:
                self._drag_overlay = None

        if style == "none":
            overlay.destroy()
            self._drag_overlay = None
        else:
            start_rect = self._drag_last_ball_rect or self._drag_source_rect or target_rect
            color = "#d8c48a" if style == "paper" else "#ffd24d"
            overlay.animate_reveal(start_rect, target_rect, color=color, on_done=_teardown)

        self._drag_source_rect = None
        self._drag_last_ball_rect = None
        self._ball_smooth_pos = None

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
