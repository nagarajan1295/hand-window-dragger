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
