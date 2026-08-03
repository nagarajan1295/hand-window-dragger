"""Guided, Face-ID-style calibration tutorial.

Walks the user through: show right hand, show left hand, make a fist,
release, then one grab-move-drop rehearsal per detected monitor -- so the
user learns the gestures instead of guessing them, and the app derives a
per-user fist-sensitivity threshold and confirms (or auto-corrects) the
camera mirror orientation from real behavior.
"""

import json
import os
import statistics
import threading
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from camera_utils import MODEL_PATH, open_camera
from gestures import curled_finger_count, is_fist, palm_center_x
from overlay import draw_landmarks
from zone_calibration import (
    CALIBRATION_LEARNING_RATE,
    load_profile,
    save_profile,
    update_center,
    zone_for_x,
)

CALIBRATION_PATH = os.path.join(os.path.dirname(__file__), "calibration.json")

HAND_POSE_HOLD_FRAMES = 20
GRAB_CONFIRM_FRAMES = 4
MAX_ZONE_ATTEMPTS_BEFORE_MIRROR_FLIP = 2


def load_calibration():
    if os.path.exists(CALIBRATION_PATH):
        try:
            with open(CALIBRATION_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_calibration(data):
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(data, f, indent=2)


def zone_label(i, n):
    if n == 1:
        return "your monitor"
    if n == 2:
        return "left" if i == 0 else "right"
    if n == 3:
        return ["left", "center", "right"][i]
    if i == 0:
        return "leftmost"
    if i == n - 1:
        return "rightmost"
    return f"monitor {i + 1} of {n}"


class CalibrationSession:
    """Runs the tutorial on a background thread.

    on_frame(frame_bgr, info) -- info has instruction/step/total/progress
    on_event(message) -- log lines for the UI
    on_complete(calibration_dict or None) -- None if cancelled
    """

    def __init__(self, monitors, starting_mirror=True, camera_index=0,
                 on_frame=None, on_event=None, on_complete=None):
        self.monitors = monitors
        self.mirror = starting_mirror
        self.camera_index = camera_index
        self.on_frame = on_frame
        self.on_event = on_event
        self.on_complete = on_complete
        self._thread = None
        self._stop_event = threading.Event()
        self.running = False
        self.steps = self._build_steps()

    def _build_steps(self):
        steps = [
            {"type": "hand_pose", "key": "right_hand", "want_fist": False,
             "instruction": "Show your right hand to the camera, palm open"},
            {"type": "hand_pose", "key": "left_hand", "want_fist": False,
             "instruction": "Now show your left hand, palm open"},
            {"type": "hand_pose", "key": "fist", "want_fist": True,
             "instruction": "Make a fist with either hand and hold it steady -- this is the GRAB gesture"},
            {"type": "hand_pose", "key": "release", "want_fist": False,
             "instruction": "Now open your hand fully -- this is the DROP gesture"},
        ]
        n = len(self.monitors)
        for i, m in enumerate(self.monitors):
            label = zone_label(i, n)
            device = m["device"].replace("\\\\.\\", "")
            steps.append({
                "type": "zone", "key": f"zone_{i}", "zone_index": i,
                "instruction": (
                    f"Make a fist to grab, move your hand to the {label.upper()} "
                    f"until {device} highlights, then open your hand to drop"
                ),
            })
        return steps

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.running = False

    def _log(self, msg):
        if self.on_event:
            self.on_event(msg)

    def _emit(self, step_idx, extra=None):
        if not self.on_frame:
            return
        # frame emission happens in the main loop with real image data;
        # this is only used to push a step-change event promptly.
        if self.on_event:
            step = self.steps[step_idx]
            self._log(f"Step {step_idx + 1}/{len(self.steps)}: {step['instruction']}")

    def _run(self):
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
        cap = open_camera(self.camera_index)
        if not cap.isOpened():
            self._log(f"Could not open camera index {self.camera_index}")
            landmarker.close()
            self.running = False
            if self.on_complete:
                self.on_complete(None)
            return

        start_ts = time.time()
        idx = 0
        hold_counter = 0
        grab_hold = 0
        sub_phase = "IDLE"
        fist_finger_counts = []
        zone_attempts = {}
        mirror_flipped = False
        zone_centers = load_profile(len(self.monitors))
        self._emit(0)

        try:
            while not self._stop_event.is_set() and idx < len(self.steps):
                ok, frame = cap.read()
                if not ok:
                    continue
                if self.mirror:
                    frame = cv2.flip(frame, 1)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int((time.time() - start_ts) * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                hand_present = len(result.hand_landmarks) > 0
                landmarks = result.hand_landmarks[0] if hand_present else None
                fist_active = is_fist(landmarks, 3) if hand_present else False
                zone = None
                hand_x = None
                if hand_present:
                    draw_landmarks(frame, landmarks)
                    hand_x = palm_center_x(landmarks)
                    zone = zone_for_x(hand_x, zone_centers)

                step = self.steps[idx]

                if step["type"] == "hand_pose":
                    want = step["want_fist"]
                    ok_pose = hand_present and (fist_active == want)
                    hold_counter = hold_counter + 1 if ok_pose else 0
                    if step["key"] == "fist" and hand_present and fist_active:
                        fist_finger_counts.append(curled_finger_count(landmarks))
                    if hold_counter >= HAND_POSE_HOLD_FRAMES:
                        self._log(f"Got it: {step['instruction']}")
                        idx += 1
                        hold_counter = 0
                        if idx < len(self.steps):
                            self._emit(idx)

                elif step["type"] == "zone":
                    target = step["zone_index"]
                    if sub_phase == "IDLE":
                        if fist_active:
                            grab_hold += 1
                            if grab_hold >= GRAB_CONFIRM_FRAMES:
                                sub_phase = "GRABBED"
                                grab_hold = 0
                        else:
                            grab_hold = 0
                    elif sub_phase == "GRABBED":
                        if not hand_present:
                            sub_phase = "IDLE"
                        elif not fist_active:
                            if zone == target:
                                self._log(f"Dropped correctly on {self.monitors[target]['device']}")
                                if hand_x is not None:
                                    zone_centers = update_center(
                                        zone_centers, target, hand_x, CALIBRATION_LEARNING_RATE
                                    )
                                    save_profile(zone_centers)
                                idx += 1
                                sub_phase = "IDLE"
                                if idx < len(self.steps):
                                    self._emit(idx)
                            else:
                                attempts = zone_attempts.get(target, 0) + 1
                                zone_attempts[target] = attempts
                                if attempts >= MAX_ZONE_ATTEMPTS_BEFORE_MIRROR_FLIP and not mirror_flipped:
                                    self.mirror = not self.mirror
                                    mirror_flipped = True
                                    self._log("That landed on the wrong monitor twice -- "
                                               "flipping mirror orientation and retrying.")
                                else:
                                    self._log("Wrong monitor -- try again.")
                                sub_phase = "IDLE"

                if self.on_frame:
                    info = {
                        "instruction": step["instruction"],
                        "step_index": idx,
                        "total_steps": len(self.steps),
                        "hand_present": hand_present,
                        "fist_active": fist_active,
                        "zone": zone,
                        "monitors": self.monitors,
                        "sub_phase": sub_phase if step["type"] == "zone" else None,
                    }
                    self.on_frame(frame, info)

        finally:
            cap.release()
            landmarker.close()
            self.running = False

        if idx >= len(self.steps):
            curled_threshold = 3
            if fist_finger_counts:
                curled_threshold = max(2, min(4, round(statistics.median(fist_finger_counts))))
            calibration = {
                "calibrated": True,
                "completed_at": time.time(),
                "mirror": self.mirror,
                "curled_threshold": curled_threshold,
                "monitors_confirmed": len(self.monitors),
            }
            save_calibration(calibration)
            self._log("Calibration complete.")
            if self.on_complete:
                self.on_complete(calibration)
        else:
            # Stopped early (user cancelled).
            if self.on_complete:
                self.on_complete(None)
