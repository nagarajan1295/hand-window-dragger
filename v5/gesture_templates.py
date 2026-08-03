"""Custom gestures: record an arbitrary static hand pose as a template and
match live landmarks against stored templates.

This is nearest-neighbor template matching on a hand-crafted, rotation- and
scale-invariant feature vector -- not a trained classifier. It reuses
MediaPipe's landmark extraction (the actual ML model) but the "is this the
gesture the user recorded" decision is a simple distance threshold, which
keeps it transparent and instantly extensible: record a pose, name it, map
it to an action, done.
"""

import json
import math
import os
import threading
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from camera_utils import MODEL_PATH, open_camera
from overlay import draw_landmarks

TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "gesture_templates.json")

# Fingertip landmark indices used to build the feature vector.
_TIPS = [4, 8, 12, 16, 20]


def _hand_scale(landmarks):
    wrist, mcp = landmarks[0], landmarks[9]
    d = math.hypot(wrist.x - mcp.x, wrist.y - mcp.y)
    return d if d > 1e-6 else 1e-6


def feature_vector(landmarks):
    """Scale-normalized distances: each fingertip to the wrist, and each
    fingertip (besides the thumb) to the thumb tip. Rotation is not fully
    invariant here (unlike the fist curl test) because pose *shape* matters
    for distinguishing gestures -- users are asked to face the camera the
    same way they will when using the gesture."""
    wrist = landmarks[0]
    thumb = landmarks[4]
    scale = _hand_scale(landmarks)
    feats = [math.hypot(landmarks[t].x - wrist.x, landmarks[t].y - wrist.y) / scale for t in _TIPS]
    feats += [math.hypot(landmarks[t].x - thumb.x, landmarks[t].y - thumb.y) / scale for t in _TIPS[1:]]
    return feats


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def load_templates():
    if os.path.exists(TEMPLATES_PATH):
        try:
            with open(TEMPLATES_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_templates(templates):
    with open(TEMPLATES_PATH, "w") as f:
        json.dump(templates, f, indent=2)


def match_gesture(landmarks, templates, threshold=0.35):
    """Return the closest template dict within `threshold`, or None."""
    if not templates:
        return None
    feats = feature_vector(landmarks)
    best, best_dist = None, threshold
    for t in templates:
        d = _distance(feats, t["features"])
        if d < best_dist:
            best, best_dist = t, d
    return best


class GestureRecorder:
    """Short-lived camera session that captures a new gesture template.

    Runs on a background thread; on_frame receives (frame_bgr, info) for a
    live preview, on_complete receives the averaged feature vector (or None
    if no hand was seen).
    """

    def __init__(self, camera_index, mirror=True, warmup_frames=20, capture_frames=45,
                 on_frame=None, on_complete=None, on_event=None):
        self.camera_index = camera_index
        self.mirror = mirror
        self.warmup_frames = warmup_frames
        self.capture_frames = capture_frames
        self.on_frame = on_frame
        self.on_complete = on_complete
        self.on_event = on_event
        self._thread = None
        self._stop_event = threading.Event()
        self.running = False

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
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

        collected = []
        frame_count = 0
        start_ts = time.time()

        try:
            while not self._stop_event.is_set() and len(collected) < self.capture_frames:
                ok, frame = cap.read()
                if not ok:
                    continue
                if self.mirror:
                    frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int((time.time() - start_ts) * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                frame_count += 1
                hand_present = len(result.hand_landmarks) > 0
                phase = "warmup" if frame_count <= self.warmup_frames else "capturing"

                if hand_present:
                    landmarks = result.hand_landmarks[0]
                    draw_landmarks(frame, landmarks)
                    if phase == "capturing":
                        collected.append(feature_vector(landmarks))

                if self.on_frame:
                    self.on_frame(frame, {
                        "phase": phase,
                        "hand_present": hand_present,
                        "progress": len(collected) / self.capture_frames,
                    })
        finally:
            cap.release()
            landmarker.close()
            self.running = False

        if collected:
            avg = [sum(v) / len(v) for v in zip(*collected)]
            self._log(f"Captured gesture from {len(collected)} frames.")
            if self.on_complete:
                self.on_complete(avg)
        else:
            self._log("No hand detected during capture -- try again.")
            if self.on_complete:
                self.on_complete(None)
