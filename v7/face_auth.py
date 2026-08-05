"""Lightweight face-recognition gate: enroll one or more people, then only
allow gestures when an enrolled face is in frame.

Uses OpenCV's bundled Haar cascade for detection and the LBPH recognizer
(cv2.face, part of opencv-contrib, already installed as a MediaPipe
dependency) for matching -- fast enough to run a few times a second on
CPU. This is a convenience gate, not a security-grade biometric system.
"""

import json
import os
import threading
import time

import cv2
import numpy as np

from camera_utils import open_camera

DATA_DIR = os.path.join(os.path.dirname(__file__), "face_data")
MODEL_PATH = os.path.join(DATA_DIR, "recognizer.yml")
LABELS_PATH = os.path.join(DATA_DIR, "labels.json")
SAMPLES_DIR = os.path.join(DATA_DIR, "samples")
FACE_SIZE = (200, 200)
RECOGNITION_THRESHOLD = 75.0  # LBPH distance -- lower means a better match

_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_detector = cv2.CascadeClassifier(_CASCADE_PATH)


def detect_face(gray_frame):
    """Return the largest detected face rect (x, y, w, h), or None."""
    faces = _detector.detectMultiScale(gray_frame, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: f[2] * f[3])


def crop_face(gray_frame, rect):
    x, y, w, h = rect
    face = gray_frame[y:y + h, x:x + w]
    return cv2.resize(face, FACE_SIZE)


def load_labels():
    if os.path.exists(LABELS_PATH):
        try:
            with open(LABELS_PATH) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except (json.JSONDecodeError, OSError, ValueError):
            return {}
    return {}


def save_labels(labels):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LABELS_PATH, "w") as f:
        json.dump({str(k): v for k, v in labels.items()}, f, indent=2)


def has_enrolled_people():
    return bool(load_labels())


def _retrain(labels):
    images, ids = [], []
    for label in labels:
        samples_dir = os.path.join(SAMPLES_DIR, str(label))
        if not os.path.isdir(samples_dir):
            continue
        for fname in os.listdir(samples_dir):
            img = cv2.imread(os.path.join(samples_dir, fname), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                images.append(img)
                ids.append(label)
    if not images:
        if os.path.exists(MODEL_PATH):
            os.remove(MODEL_PATH)
        return
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, np.array(ids))
    recognizer.save(MODEL_PATH)


def enroll_person(name, face_samples):
    """face_samples: list of grayscale FACE_SIZE crops. Adds a new labeled
    person and retrains on everyone enrolled so far -- LBPH has no cheap
    way to add one person without the others' samples on hand, so those
    are kept on disk under face_data/samples/<label>/."""
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    labels = load_labels()
    new_label = max(labels.keys(), default=-1) + 1
    labels[new_label] = name
    save_labels(labels)

    person_dir = os.path.join(SAMPLES_DIR, str(new_label))
    os.makedirs(person_dir, exist_ok=True)
    for i, face in enumerate(face_samples):
        cv2.imwrite(os.path.join(person_dir, f"{i}.png"), face)

    _retrain(labels)
    return new_label


def remove_person(label):
    labels = load_labels()
    if label not in labels:
        return
    del labels[label]
    save_labels(labels)
    person_dir = os.path.join(SAMPLES_DIR, str(label))
    if os.path.isdir(person_dir):
        for fname in os.listdir(person_dir):
            os.remove(os.path.join(person_dir, fname))
        os.rmdir(person_dir)
    _retrain(labels)


class FaceRecognizerGate:
    """Loads the trained recognizer once; call recognize(gray_frame) on
    each check to see whether an enrolled face is currently in view."""

    def __init__(self):
        self.labels = load_labels()
        self.recognizer = None
        if self.labels and os.path.exists(MODEL_PATH):
            self.recognizer = cv2.face.LBPHFaceRecognizer_create()
            self.recognizer.read(MODEL_PATH)

    def reload(self):
        self.__init__()

    def recognize(self, gray_frame):
        """Return (name_or_None, confidence_or_None, rect_or_None).

        rect is the detected face's bounding box whenever ANY face is
        found, regardless of whether it matches an enrolled identity --
        callers that only care about presence (not identity), like
        gesture face-gating or hand-near-face suppression, can use rect
        alone without a second cascade pass."""
        rect = detect_face(gray_frame)
        if rect is None:
            return None, None, None
        if not self.recognizer:
            return None, None, rect
        face = crop_face(gray_frame, rect)
        label, confidence = self.recognizer.predict(face)
        if confidence <= RECOGNITION_THRESHOLD and label in self.labels:
            return self.labels[label], confidence, rect
        return None, None, rect


class FaceEnrollmentSession:
    """Captures face samples across a few head-turn prompts (more robust
    recognition than a single straight-on pose) and enrolls the person.

    on_frame(frame_bgr, info), on_event(message), on_complete(label_or_None).
    """

    PROMPTS = [
        ("center", "Look straight at the camera"),
        ("left", "Slowly turn your head to the left"),
        ("right", "Slowly turn your head to the right"),
    ]
    SAMPLES_PER_PROMPT = 20
    GET_READY_SECONDS = 2.5   # time to get into position before capture starts
    MIN_CAPTURE_SECONDS = 3.5  # capture runs at least this long even if samples fill up sooner
    SAMPLE_INTERVAL_SECONDS = 0.12  # throttle so samples spread across the full duration

    def __init__(self, name, camera_index=0, mirror=True, on_frame=None, on_event=None, on_complete=None):
        self.name = name
        self.camera_index = camera_index
        self.mirror = mirror
        self.on_frame = on_frame
        self.on_event = on_event
        self.on_complete = on_complete
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

    def cancel(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.running = False

    def _log(self, msg):
        if self.on_event:
            self.on_event(msg)

    def _run(self):
        cap = open_camera(self.camera_index)
        if not cap.isOpened():
            self._log(f"Could not open camera index {self.camera_index}")
            self.running = False
            if self.on_complete:
                self.on_complete(None)
            return

        samples = []  # list of (prompt_key, face_crop)
        prompt_idx = 0
        phase = "ready"  # "ready" (countdown) -> "capture"
        phase_start = time.time()
        last_sample_ts = 0.0

        try:
            while not self._stop_event.is_set() and prompt_idx < len(self.PROMPTS):
                ok, frame = cap.read()
                if not ok:
                    continue
                if self.mirror:
                    frame = cv2.flip(frame, 1)

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                rect = detect_face(gray)
                key, base_instruction = self.PROMPTS[prompt_idx]
                now = time.time()
                elapsed = now - phase_start

                if rect is not None:
                    x, y, w, h = rect
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                if phase == "ready":
                    remaining = max(0.0, self.GET_READY_SECONDS - elapsed)
                    instruction = f"Get ready: {base_instruction}"
                    progress = 0.0
                    if remaining <= 0:
                        phase = "capture"
                        phase_start = now
                else:
                    collected = sum(1 for s in samples if s[0] == key)
                    if rect is not None and (now - last_sample_ts) >= self.SAMPLE_INTERVAL_SECONDS \
                            and collected < self.SAMPLES_PER_PROMPT:
                        samples.append((key, crop_face(gray, rect)))
                        last_sample_ts = now
                        collected += 1
                    instruction = base_instruction
                    progress = min(1.0, min(collected / self.SAMPLES_PER_PROMPT, elapsed / self.MIN_CAPTURE_SECONDS))
                    if collected >= self.SAMPLES_PER_PROMPT and elapsed >= self.MIN_CAPTURE_SECONDS:
                        self._log(f"Got it: {base_instruction}")
                        prompt_idx += 1
                        phase = "ready"
                        phase_start = now

                if self.on_frame:
                    self.on_frame(frame, {
                        "instruction": instruction,
                        "prompt_index": prompt_idx,
                        "total_prompts": len(self.PROMPTS),
                        "progress": progress,
                        "face_detected": rect is not None,
                    })
        finally:
            cap.release()
            self.running = False

        if prompt_idx >= len(self.PROMPTS):
            face_samples = [f for _key, f in samples]
            label = enroll_person(self.name, face_samples)
            self._log(f"Enrolled '{self.name}' from {len(face_samples)} samples.")
            if self.on_complete:
                self.on_complete(label)
        else:
            if self.on_complete:
                self.on_complete(None)
