"""Gesture classification from MediaPipe HandLandmarker output.

Landmark indices (MediaPipe Hands, 21 points per hand):
  0 wrist, 4 thumb tip, 5/6/7/8 index (MCP/PIP/DIP/tip),
  9/10/11/12 middle, 13/14/15/16 ring, 17/18/19/20 pinky.
"""

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # index
    (5, 9), (9, 10), (10, 11), (11, 12),     # middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # ring
    (13, 17), (17, 18), (18, 19), (19, 20),  # pinky
    (0, 17),                                 # palm base
]

# (pip_index, tip_index) for the four fingers used in the fist test.
_FINGER_JOINTS = [(6, 8), (10, 12), (14, 16), (18, 20)]


def _dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def curled_finger_count(landmarks):
    """Count fingers (of 4) whose tip sits closer to the wrist than its PIP
    joint does -- rotation-invariant, true regardless of how the hand is
    turned toward the camera, unlike a simple tip.y < pip.y check."""
    wrist = landmarks[0]
    curled = 0
    for pip_idx, tip_idx in _FINGER_JOINTS:
        if _dist(landmarks[tip_idx], wrist) < _dist(landmarks[pip_idx], wrist):
            curled += 1
    return curled


def is_fist(landmarks, curled_threshold=3):
    """Grab detector: True once at least curled_threshold fingers are curled."""
    return curled_finger_count(landmarks) >= curled_threshold


def palm_center_x(landmarks):
    """Stable horizontal reference point (middle-finger MCP)."""
    return landmarks[9].x
