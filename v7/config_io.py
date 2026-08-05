"""Shared config defaults + JSON persistence."""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
CALIBRATION_PATH = os.path.join(os.path.dirname(__file__), "calibration.json")

DEFAULT_CONFIG = {
    "camera_index": 0,
    "mirror": True,
    "maximize_on_drop": True,
    "curled_threshold": 3,      # fingers (of 4) that must curl to count as a fist
    "grab_debounce_frames": 4,  # frames the fist must hold before a grab registers
    "release_debounce_frames": 3,
    "lost_grace_frames": 15,    # frames a hand can vanish before a grab auto-cancels
    "gesture_hold_frames": 10,     # frames a custom gesture must hold before it fires
    "gesture_cooldown_frames": 45,  # frames to wait before the same gesture can refire
    "gesture_match_threshold": 0.35,
    "zone_based_grab": True,   # grab whatever window is topmost on the hand's monitor,
                                # instead of whatever last had OS focus
    "face_lock_enabled": False,  # only allow gestures when an enrolled face is recognized
    "face_check_interval_frames": 5,
    "face_lock_grace_frames": 20,  # frames a recognized face may drop out of view before locking again
    # Drag animation layers -- follow_style is a single choice (it's the
    # appearance of one moving object, so paper/portal can't both be it
    # at once), the rest are independent toggles combinable with it and
    # with each other.
    "follow_style": "none",  # "none" (default) | "paper" | "portal"
    "highlight_target_enabled": False,
    "show_name_label": False,
    "particle_trail_enabled": False,
    # Presence: turn the display off when no enrolled face has been seen
    # for a while, back on (with an optional greeting) when it returns.
    # Not lock, not system sleep -- just the monitor's power state.
    "presence_display_control_enabled": False,
    "presence_greeting_enabled": False,
    "presence_absence_seconds": 20,
    # Greeting customization -- see greeting.py / greeting_ui.py.
    "greeting_text": "Hi {name}",             # {name} is replaced with the recognized person's name
    "greeting_font": "Claude-style (Segoe UI Semilight)",
    "greeting_text_color": "#eee7dd",
    "greeting_bg_shape": "glow_orb",          # "glow_orb" | "rounded_rect" | "rectangle" | "none"
    "greeting_bg_color": "#a85c42",
    "greeting_use_custom_image": False,       # show an uploaded image instead of text
    "greeting_image_path": "",
    # Reduce false-positive gestures.
    "require_face_for_gestures": False,   # a face must be detected for any gesture to register
    "suppress_hand_near_face": False,     # ignore hand poses that overlap the detected face (scratching, etc.)
    # Usage-pattern learning: logs drop events and trains a small
    # incremental neural network (sklearn MLPClassifier) to recognize
    # patterns. Surfaces suggestions in the activity log; never acts on
    # its own.
    "pattern_learning_enabled": False,
}


def load_config():
    # Precedence: built-in defaults < learned calibration < explicit saved
    # settings (config.json wins if the user has since tuned a slider).
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CALIBRATION_PATH):
        try:
            with open(CALIBRATION_PATH, "r") as f:
                calib = json.load(f)
            for key in ("mirror", "curled_threshold"):
                if key in calib:
                    cfg[key] = calib[key]
        except (json.JSONDecodeError, OSError):
            pass
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
