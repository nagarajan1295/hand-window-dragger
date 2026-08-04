"""Learns per-user monitor-zone boundaries from observed hand positions,
instead of assuming the camera frame splits evenly across monitors.

Where someone's hand naturally sits when they mean "the left monitor"
vs. "the right monitor" depends on their desk layout, camera placement,
and field of view -- a fixed even split of the frame doesn't match that
for everyone, which is exactly the "I drop where the monitor isn't"
complaint this addresses. Instead, each zone has a running-average
"center" hand_x, updated a little every time a drop is confirmed for
that zone (both during the calibration tutorial, which seeds it with
confident direct observations, and during ordinary use afterward, which
keeps nudging it as the person's actual habits emerge). Zone boundaries
are just the midpoints between adjacent learned centers.
"""

import json
import os

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "zone_profile.json")

# Ordinary-use drops nudge a zone's center gently (it's one data point
# among many, and could be an imprecise/rushed drop).
USAGE_LEARNING_RATE = 0.12
# Calibration-tutorial confirmations are a deliberate, attentive action
# repeated for that exact zone, so they carry a lot more weight.
CALIBRATION_LEARNING_RATE = 0.6


def _default_centers(num_zones):
    return [(i + 0.5) / num_zones for i in range(num_zones)]


def load_profile(num_zones):
    """Return a list of `num_zones` hand_x centers (0..1), seeded to an
    even split if there's no saved profile or the monitor count changed
    since it was learned."""
    if os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH) as f:
                data = json.load(f)
            centers = data.get("centers")
            if isinstance(centers, list) and len(centers) == num_zones:
                return [float(c) for c in centers]
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return _default_centers(num_zones)


def save_profile(centers):
    with open(PROFILE_PATH, "w") as f:
        json.dump({"centers": centers}, f, indent=2)


def reset_profile(num_zones):
    centers = _default_centers(num_zones)
    save_profile(centers)
    return centers


def zone_boundaries(centers):
    """Zone i covers hand_x in [boundaries[i], boundaries[i+1]). Centers
    must be sorted ascending (left-to-right zone order) for this to make
    sense, same as the monitor list it mirrors."""
    bounds = [0.0]
    for i in range(len(centers) - 1):
        bounds.append((centers[i] + centers[i + 1]) / 2)
    bounds.append(1.0)
    return bounds


def zone_for_x(x, centers):
    bounds = zone_boundaries(centers)
    for i in range(len(centers)):
        if bounds[i] <= x < bounds[i + 1]:
            return i
    return len(centers) - 1


def update_center(centers, zone, x, rate):
    updated = list(centers)
    updated[zone] = updated[zone] * (1 - rate) + x * rate
    return updated
