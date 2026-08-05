"""UI-agnostic hand-tracking + window-drag engine.

Runs the camera/MediaPipe/gesture loop on a background thread and reports
frames + events through callbacks, so any front end (CLI, Tkinter, etc.)
can drive it without touching OpenCV/MediaPipe/Win32 directly.
"""

import datetime
import os
import threading
import time

import cv2
import mediapipe as mp
import win32gui
import win32process
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from actions import action_label, fire_action
from camera_utils import MODEL_PATH, open_camera
from config_io import DEFAULT_CONFIG
from face_auth import FaceRecognizerGate
from gesture_templates import load_templates, match_gesture
from gestures import is_fist, palm_center_x
from pattern_learning import PatternLearner, log_event as log_pattern_event
from window_manager import (
    get_monitors_sorted,
    get_topmost_window_on_monitor,
    is_real_window,
    monitor_index_for_window,
    move_window_to_monitor,
    prevent_system_idle_lock,
    set_monitor_power,
    simulate_trivial_input,
)
from zone_calibration import USAGE_LEARNING_RATE, load_profile, save_profile, update_center, zone_for_x

# If the hand's last known horizontal position before it left the frame
# was within this fraction of either edge, treat it as having exited that
# side (and drop there) rather than as tracking just being lost.
EDGE_EXIT_FRACTION = 0.08

# Consecutive face-check misses tolerated before "require a face for
# gestures" kicks in -- a small grace window so momentary detection
# flicker (bad angle, blink, camera noise) doesn't cut off mid-gesture.
REQUIRE_FACE_GRACE_CHECKS = 2

# Face box is expanded by this fraction on each side before checking hand
# overlap -- "near" the face, not just literally touching its exact edges.
NEAR_FACE_MARGIN_FRACTION = 0.35


def _expand_rect(rect, margin_frac):
    x, y, w, h = rect
    mx, my = w * margin_frac, h * margin_frac
    return (x - mx, y - my, x + w + mx, y + h + my)


def _boxes_overlap(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


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
        self._face_gate = FaceRecognizerGate()
        self._zone_centers = load_profile(len(self.monitors))
        self._pattern_learner = None  # created lazily -- only if the toggle is ever turned on

    def cycle_camera(self):
        self.config["camera_index"] = (self.config["camera_index"] + 1) % 4

    def reload_templates(self):
        """Call after adding/removing a custom gesture so a running engine
        picks it up without a restart."""
        self._templates = load_templates()

    def reload_face_gate(self):
        """Call after enrolling/removing an enrolled face."""
        self._face_gate.reload()

    def reload_zone_profile(self):
        """Call after the calibration tutorial updates the learned zone
        centers so a running engine picks them up without a restart."""
        self._zone_centers = load_profile(len(self.monitors))

    def _ensure_pattern_learner(self):
        if self._pattern_learner is None:
            self._pattern_learner = PatternLearner(len(self.monitors))
        return self._pattern_learner

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
        grabbed_source_zone = None
        hold_frames = 0
        release_frames = 0
        lost_frames = 0
        zone = None
        hand_x = None
        hand_y = None
        start_ts = time.time()

        last_gesture_key = None
        gesture_hold = 0
        gesture_cooldown = 0
        active_gesture_name = None

        face_authorized = not cfg["face_lock_enabled"]
        face_name = None
        face_lost_frames = 0
        face_check_counter = 0
        face_present = False
        face_absent_checks = 0
        last_face_rect = None
        near_face_suppressed = False

        display_off = False
        face_seen_last_ts = time.time()  # seed so display doesn't blank immediately at startup
        greeting_pending = False
        greeting_name = None
        idle_lock_suppressed = False
        last_input_sim_ts = 0.0

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

                greeting_pending = False
                face_features_needed = (
                    cfg["face_lock_enabled"] or cfg["require_face_for_gestures"]
                    or cfg["suppress_hand_near_face"] or cfg["presence_display_control_enabled"]
                )
                if face_features_needed:
                    face_check_counter += 1
                    if face_check_counter >= cfg["face_check_interval_frames"]:
                        face_check_counter = 0
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        name, _confidence, rect = self._face_gate.recognize(gray)
                        last_face_rect = rect
                        face_present = rect is not None
                        face_absent_checks = 0 if face_present else face_absent_checks + 1

                        if cfg["face_lock_enabled"]:
                            if name:
                                face_authorized = True
                                face_name = name
                                face_lost_frames = 0
                            else:
                                face_lost_frames += 1
                                if face_lost_frames > cfg["face_lock_grace_frames"]:
                                    face_authorized = False
                                    face_name = None
                        else:
                            face_authorized = True
                            face_name = None

                        if cfg["presence_display_control_enabled"]:
                            now = time.time()
                            if name:
                                face_seen_last_ts = now
                                if display_off:
                                    set_monitor_power(True)
                                    display_off = False
                                    self._log(f"Welcome back, {name} -- display on.")
                                    if cfg["presence_greeting_enabled"]:
                                        greeting_pending = True
                                        greeting_name = name
                            elif not display_off and (now - face_seen_last_ts) > cfg["presence_absence_seconds"]:
                                set_monitor_power(False)
                                display_off = True
                                self._log("No one detected -- display off.")
                else:
                    face_authorized = not cfg["face_lock_enabled"]
                    face_name = None
                    face_present = False
                    face_absent_checks = 0
                    last_face_rect = None

                # Keep this in sync with the toggle every iteration (not
                # just on face-check ticks) so it reacts immediately if
                # the setting changes mid-run.
                if cfg["presence_display_control_enabled"] and not idle_lock_suppressed:
                    prevent_system_idle_lock(True)
                    idle_lock_suppressed = True
                elif not cfg["presence_display_control_enabled"] and idle_lock_suppressed:
                    prevent_system_idle_lock(False)
                    idle_lock_suppressed = False

                # The screensaver's own idle timer (and any OS-level
                # inactivity-lock policy) is driven by real keyboard/mouse
                # input, not by the power-idle state prevent_system_idle_lock
                # suppresses above -- so while we've deliberately blanked the
                # display because no one is present, also nudge that timer
                # every ~20s so a "require sign-in" screensaver doesn't lock
                # the session out from under the display-off feature.
                if display_off:
                    now_sim = time.time()
                    if now_sim - last_input_sim_ts > 20:
                        simulate_trivial_input()
                        last_input_sim_ts = now_sim

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
                near_face_suppressed = False
                if hand_present:
                    landmarks = result.hand_landmarks[0]
                    fist_active = is_fist(landmarks, cfg["curled_threshold"])
                    hand_x = palm_center_x(landmarks)
                    hand_y = landmarks[9].y
                    zone = zone_for_x(hand_x, self._zone_centers)
                    lost_frames = 0

                    if cfg["suppress_hand_near_face"] and last_face_rect is not None:
                        fh, fw = frame.shape[:2]
                        xs = [lm.x * fw for lm in landmarks]
                        ys = [lm.y * fh for lm in landmarks]
                        hand_box = (min(xs), min(ys), max(xs), max(ys))
                        if _boxes_overlap(hand_box, _expand_rect(last_face_rect, NEAR_FACE_MARGIN_FRACTION)):
                            near_face_suppressed = True
                            fist_active = False  # ignore face-touching poses (scratching, etc.) as a grab
                else:
                    lost_frames += 1

                # A face must actually be in frame for gestures to count
                # at all, when that toggle is on -- a small grace window
                # (checked in face-check-interval units, not frames) so
                # momentary detection flicker doesn't cut a gesture off
                # mid-motion.
                gestures_allowed = (
                    face_authorized
                    and not near_face_suppressed
                    and (not cfg["require_face_for_gestures"] or face_absent_checks <= REQUIRE_FACE_GRACE_CHECKS)
                )

                if state == "IDLE":
                    if fist_active and gestures_allowed:
                        hold_frames += 1
                        if hold_frames >= cfg["grab_debounce_frames"]:
                            if cfg["zone_based_grab"] and zone is not None:
                                # Grab whatever window is currently topmost on
                                # the monitor under the hand, regardless of
                                # which app last had OS focus -- so re-grabbing
                                # the same spot after moving a window away
                                # automatically picks up whatever's now on top
                                # there instead of staying "locked" to it.
                                target = get_topmost_window_on_monitor(self.monitors[zone], exclude_pid=my_pid)
                            else:
                                target = last_external_fg
                            if target and win32gui.IsWindow(target):
                                grabbed_hwnd = target
                                grabbed_title = win32gui.GetWindowText(grabbed_hwnd)
                                grabbed_source_zone = monitor_index_for_window(grabbed_hwnd, self.monitors)
                                state = "HOLDING"
                                self._log(f"Grabbed '{grabbed_title}'")
                                if cfg["pattern_learning_enabled"]:
                                    now_dt = datetime.datetime.now()
                                    prediction = self._ensure_pattern_learner().predict(
                                        grabbed_title, grabbed_source_zone, now_dt.hour, now_dt.weekday()
                                    )
                                    if prediction:
                                        pred_zone, confidence = prediction
                                        self._log(
                                            f"Pattern: you usually drop '{grabbed_title}' on "
                                            f"{self.monitors[pred_zone]['device']} around this time "
                                            f"({confidence * 100:.0f}% confidence)."
                                        )
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
                                # A deliberate, in-frame drop is a confident
                                # data point for "this is where this user's
                                # hand sits when they mean this monitor" --
                                # nudge the learned zone center toward it.
                                if hand_x is not None:
                                    self._zone_centers = update_center(
                                        self._zone_centers, zone, hand_x, USAGE_LEARNING_RATE
                                    )
                                    save_profile(self._zone_centers)
                                if cfg["pattern_learning_enabled"]:
                                    now_dt = datetime.datetime.now()
                                    self._ensure_pattern_learner().learn(
                                        grabbed_title, grabbed_source_zone, zone, now_dt.hour, now_dt.weekday()
                                    )
                                    log_pattern_event(grabbed_title, grabbed_source_zone, zone)
                            grabbed_hwnd = None
                            grabbed_title = ""
                            grabbed_source_zone = None
                            state = "IDLE"
                            release_frames = 0
                    else:
                        release_frames = 0

                    if lost_frames > cfg["lost_grace_frames"]:
                        exited_left = hand_x is not None and hand_x <= EDGE_EXIT_FRACTION
                        exited_right = hand_x is not None and hand_x >= 1 - EDGE_EXIT_FRACTION
                        if grabbed_hwnd and (exited_left or exited_right):
                            edge_zone = 0 if exited_left else len(self.monitors) - 1
                            move_window_to_monitor(grabbed_hwnd, self.monitors[edge_zone], cfg["maximize_on_drop"])
                            self._log(
                                f"Hand left the frame; dropped '{grabbed_title}' "
                                f"-> {self.monitors[edge_zone]['device']}"
                            )
                            if cfg["pattern_learning_enabled"]:
                                now_dt = datetime.datetime.now()
                                self._ensure_pattern_learner().learn(
                                    grabbed_title, grabbed_source_zone, edge_zone, now_dt.hour, now_dt.weekday()
                                )
                                log_pattern_event(grabbed_title, grabbed_source_zone, edge_zone)
                        else:
                            self._log("Hand lost; grab cancelled.")
                        grabbed_hwnd = None
                        grabbed_title = ""
                        grabbed_source_zone = None
                        state = "IDLE"
                        release_frames = 0

                # Custom gestures only run outside of the grab/drag flow, and
                # never on a fist (that pose is reserved for grabbing).
                active_gesture_name = None
                if state == "IDLE" and gestures_allowed and hand_present and not fist_active and landmarks is not None:
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
                        try:
                            fire_action(match)
                            self._log(f"Gesture '{match['name']}' -> {action_label(match)}")
                        except (OSError, KeyError, ValueError) as e:
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
                        "grabbed_hwnd": grabbed_hwnd,
                        "grabbed_title": grabbed_title,
                        "hand_x": hand_x,
                        "hand_y": hand_y,
                        "landmarks": landmarks,
                        "monitors": self.monitors,
                        "active_gesture_name": active_gesture_name,
                        "gesture_hold": gesture_hold,
                        "gesture_hold_needed": cfg["gesture_hold_frames"],
                        "face_lock_enabled": cfg["face_lock_enabled"],
                        "face_authorized": face_authorized,
                        "face_name": face_name,
                        "face_present": face_present,
                        "near_face_suppressed": near_face_suppressed,
                        "zone_centers": self._zone_centers,
                        "greeting_pending": greeting_pending,
                        "greeting_name": greeting_name,
                    }
                    self.on_frame(frame, info)
                else:
                    time.sleep(0.001)

        finally:
            if display_off:
                set_monitor_power(True)  # don't leave the screen blank after tracking stops
            if idle_lock_suppressed:
                prevent_system_idle_lock(False)  # restore normal Windows idle/lock behavior
            cap.release()
            landmarker.close()
            self.running = False
            self._log("Stopped.")
