"""UI-agnostic hand-tracking + window-drag engine.

Runs the camera/MediaPipe/gesture loop on a background thread and reports
frames + events through callbacks, so any front end (CLI, Tkinter, etc.)
can drive it without touching OpenCV/MediaPipe/Win32 directly.
"""

import os
import threading
import time

import cv2
import mediapipe as mp
import win32gui
import win32process
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from actions import ACTIONS
from camera_utils import MODEL_PATH, open_camera
from config_io import DEFAULT_CONFIG
from gesture_templates import load_templates, match_gesture
from gestures import is_fist, palm_center_x
from window_manager import get_monitors_sorted, is_real_window, move_window_to_monitor


class HandDraggerEngine:
    """config is a live dict -- mutate its values (e.g. from GUI sliders) and
    the running loop picks up the change on its next iteration."""

    def __init__(self, config=None, on_frame=None, on_event=None):
        # Keep the caller's dict object (don't copy) so external mutation --
        # e.g. a GUI slider or the CLI's key handlers -- is visible to the
        # running loop on its next iteration.
        self.config = config if config is not None else {}
        for key, value in DEFAULT_CONFIG.items():
            self.config.setdefault(key, value)
        self.on_frame = on_frame  # callback(frame_bgr, info_dict)
        self.on_event = on_event  # callback(message_str)
        self.monitors = get_monitors_sorted()
        self.running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._templates = load_templates()

    def cycle_camera(self):
        self.config["camera_index"] = (self.config["camera_index"] + 1) % 4

    def reload_templates(self):
        """Call after adding/removing a custom gesture so a running engine
        picks it up without a restart."""
        self._templates = load_templates()

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.running = True
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.running = False

    def _log(self, msg):
        if self.on_event:
            self.on_event(msg)

    def _run(self):
        cfg = self.config
        base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = vision.HandLandmarker.create_from_options(options)

        current_cam_index = cfg["camera_index"]
        cap = open_camera(current_cam_index)
        if not cap.isOpened():
            self._log(f"Could not open camera index {current_cam_index}")
            landmarker.close()
            self.running = False
            return

        my_pid = os.getpid()
        last_external_fg = None
        state = "IDLE"
        grabbed_hwnd = None
        grabbed_title = ""
        hold_frames = 0
        release_frames = 0
        lost_frames = 0
        zone = None
        start_ts = time.time()

        last_gesture_key = None
        gesture_hold = 0
        gesture_cooldown = 0
        active_gesture_name = None

        self._log(f"Started. {len(self.monitors)} monitor(s) detected.")

        try:
            while not self._stop_event.is_set():
                if cfg["camera_index"] != current_cam_index:
                    cap.release()
                    current_cam_index = cfg["camera_index"]
                    cap = open_camera(current_cam_index)
                    if not cap.isOpened():
                        self._log(f"Could not open camera index {current_cam_index}")

                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                if cfg["mirror"]:
                    frame = cv2.flip(frame, 1)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int((time.time() - start_ts) * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                # Track the last real (non-overlay) foreground window every
                # frame, so a grab always targets whatever app the user
                # actually had focused.
                fg = win32gui.GetForegroundWindow()
                if is_real_window(fg):
                    _tid, pid = win32process.GetWindowThreadProcessId(fg)
                    if pid != my_pid:
                        last_external_fg = fg

                hand_present = len(result.hand_landmarks) > 0
                fist_active = False
                landmarks = None
                if hand_present:
                    landmarks = result.hand_landmarks[0]
                    fist_active = is_fist(landmarks, cfg["curled_threshold"])
                    zone = int(palm_center_x(landmarks) * len(self.monitors))
                    zone = max(0, min(len(self.monitors) - 1, zone))
                    lost_frames = 0
                else:
                    lost_frames += 1

                if state == "IDLE":
                    if fist_active:
                        hold_frames += 1
                        if hold_frames >= cfg["grab_debounce_frames"]:
                            if last_external_fg and win32gui.IsWindow(last_external_fg):
                                grabbed_hwnd = last_external_fg
                                grabbed_title = win32gui.GetWindowText(grabbed_hwnd)
                                state = "HOLDING"
                                self._log(f"Grabbed '{grabbed_title}'")
                            hold_frames = 0
                    else:
                        hold_frames = 0

                elif state == "HOLDING":
                    if not fist_active:
                        release_frames += 1
                        if release_frames >= cfg["release_debounce_frames"]:
                            if grabbed_hwnd and zone is not None:
                                move_window_to_monitor(
                                    grabbed_hwnd, self.monitors[zone], cfg["maximize_on_drop"]
                                )
                                self._log(f"Dropped '{grabbed_title}' -> {self.monitors[zone]['device']}")
                            grabbed_hwnd = None
                            grabbed_title = ""
                            state = "IDLE"
                            release_frames = 0
                    else:
                        release_frames = 0

                    if lost_frames > cfg["lost_grace_frames"]:
                        self._log("Hand lost; grab cancelled.")
                        grabbed_hwnd = None
                        grabbed_title = ""
                        state = "IDLE"
                        release_frames = 0

                # Custom gestures only run outside of the grab/drag flow, and
                # never on a fist (that pose is reserved for grabbing).
                active_gesture_name = None
                if state == "IDLE" and hand_present and not fist_active and landmarks is not None:
                    match = match_gesture(landmarks, self._templates, cfg["gesture_match_threshold"])
                    match_key = match["key"] if match else None
                    if match_key and match_key == last_gesture_key:
                        gesture_hold += 1
                    elif match_key:
                        last_gesture_key = match_key
                        gesture_hold = 1
                    else:
                        last_gesture_key = None
                        gesture_hold = 0

                    if gesture_cooldown > 0:
                        gesture_cooldown -= 1
                    elif match and gesture_hold >= cfg["gesture_hold_frames"]:
                        active_gesture_name = match["name"]
                        action_entry = ACTIONS.get(match["action"])
                        if action_entry:
                            label, action_fn = action_entry
                            try:
                                action_fn()
                                self._log(f"Gesture '{match['name']}' -> {label}")
                            except OSError as e:
                                self._log(f"Gesture action failed: {e}")
                        gesture_cooldown = cfg["gesture_cooldown_frames"]
                        gesture_hold = 0
                    elif match:
                        active_gesture_name = match["name"]
                else:
                    last_gesture_key = None
                    gesture_hold = 0

                if self.on_frame:
                    info = {
                        "state": state,
                        "zone": zone,
                        "hand_present": hand_present,
                        "fist_active": fist_active,
                        "grabbed_title": grabbed_title,
                        "landmarks": landmarks,
                        "monitors": self.monitors,
                        "active_gesture_name": active_gesture_name,
                        "gesture_hold": gesture_hold,
                        "gesture_hold_needed": cfg["gesture_hold_frames"],
                    }
                    self.on_frame(frame, info)
                else:
                    time.sleep(0.001)

        finally:
            cap.release()
            landmarker.close()
            self.running = False
            self._log("Stopped.")
