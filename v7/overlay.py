"""Shared frame-annotation helpers used by both the CLI and GUI front ends."""

import cv2

from gestures import HAND_CONNECTIONS


def draw_landmarks(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 180, 0), 2)
    for p in pts:
        cv2.circle(frame, p, 4, (0, 255, 0), -1)


def draw_overlay(frame, monitors, zone, state, grabbed_title, fist_active, hand_present,
                  active_gesture_name=None, gesture_hold=0, gesture_hold_needed=0,
                  face_lock_enabled=False, face_authorized=True, face_name=None):
    h, w = frame.shape[:2]
    n = len(monitors)

    for i in range(1, n):
        x = int(w * i / n)
        cv2.line(frame, (x, 0), (x, h), (80, 80, 80), 1)

    for i, m in enumerate(monitors):
        cx = int(w * (i + 0.5) / n)
        active = state == "HOLDING" and zone == i
        color = (0, 255, 255) if active else (120, 120, 120)
        cv2.putText(frame, m["device"].replace("\\\\.\\", ""), (cx - 40, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        if active:
            band = frame.copy()
            cv2.rectangle(band, (int(w * i / n), 0), (int(w * (i + 1) / n), h), (0, 200, 200), -1)
            cv2.addWeighted(band, 0.12, frame, 0.88, 0, frame)

    status = f"state={state}  hand={'yes' if hand_present else 'no'}  fist={'yes' if fist_active else 'no'}"
    cv2.putText(frame, status, (10, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    if grabbed_title:
        cv2.putText(frame, f"holding: {grabbed_title[:50]}", (10, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
    elif active_gesture_name:
        cv2.putText(frame, f"gesture: {active_gesture_name} ({gesture_hold}/{gesture_hold_needed})",
                    (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1)

    if face_lock_enabled:
        if face_authorized:
            label, color = f"unlocked: {face_name}" if face_name else "unlocked", (0, 220, 0)
        else:
            label, color = "LOCKED: face not recognized", (0, 0, 255)
        cv2.putText(frame, label, (w - 260, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
