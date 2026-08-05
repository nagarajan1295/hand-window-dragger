# Hand Window Dragger (v7)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

Everything in [v6](../v6) (calibration tutorial, zone-based grabbing,
face-recognition security, custom gestures including keyboard/mouse
actions, portal/paper drag animations with HUD highlighting), plus:
presence-aware display power control with a JARVIS-style greeting,
two features to cut down false-positive gestures, and a small
incrementally-trained neural network that learns your drag/drop habits.

Built on [MediaPipe HandLandmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)
(Google, open source) for hand tracking, OpenCV for camera capture and
face detection/recognition (Haar cascade + LBPH, via opencv-contrib),
scikit-learn for the pattern-learning model, and pywin32 for window
placement and system actions. Windows only.

## Requirements

- Windows with a webcam
- Python 3.10+
- `pip install -r requirements.txt` (adds `scikit-learn` and `joblib`
  over v6, for pattern learning)

## Run it

Double-click `launch_gui.pyw`, or `pythonw launch_gui.pyw`, or the CLI
with `python main.py`.

## What's new in v7

### Presence: display on/off, not lock

Settings -> **Presence** section. **"Turn display off when I leave, on
when I return"** watches for your enrolled face; after it hasn't seen
you for the configured **away threshold** (default 20s), it turns the
monitor(s) off -- not lock, not system sleep, just display power, via
the standard `WM_SYSCOMMAND`/`SC_MONITORPOWER` call. The camera and app
keep running underneath the whole time, so it notices the moment you're
back and turns the display on again. Requires at least one enrolled
face (the toggle refuses to turn on otherwise -- without a recognized
identity to watch for, the display would turn off once and never find a
reason to turn back on).

**"Show a greeting when the display turns back on"** shows a big
JARVIS-HUD-style "HI \<YOUR NAME\>" in cyan, holds briefly, then fades
out over a couple seconds (`greeting.py`) -- only fires alongside the
display turning back on from a recognized face, not on every random
face-detection blip.

Note: like every other feature here, this only runs while **Start
Tracking** is active -- it's part of the same camera loop, not a
separate always-on background service.

### Fewer false-positive gestures

Settings -> **Gesture safety**:
- **"Require a face to be detected for any gesture"** -- grabs and
  custom gestures are ignored unless a face (not necessarily identified,
  just present) is currently in frame.
- **"Ignore hand gestures near my face"** -- if your hand overlaps the
  detected face area (with some margin), that frame's pose is treated as
  not-a-gesture, so scratching your face, adjusting glasses, etc. won't
  get misread as a fist/grab or a custom gesture.

Honest limitation: this doesn't verify a hand belongs to the specific
person whose face is visible (that would need body/pose tracking, not
just face + hand detection) -- someone else's hand while your face
happens to still be in frame isn't fully ruled out. It does stop the
face-touching false positives and reduces (though doesn't eliminate) the
"someone else's hand" case, since their reach usually disrupts your own
face's detection too.

### Pattern learning

Settings -> **Pattern learning** -> **"Learn my drag/drop patterns"**.
Every confirmed drop (app, source zone, target zone, time of day) is
logged to `usage_history.jsonl`, and a small neural network
(`sklearn.neural_network.MLPClassifier`, trained via `partial_fit` so it
updates after every single drop rather than needing a batch retrain)
learns which zone a given app tends to end up on at a given time of day.

This is a genuinely real, if small, neural network -- appropriately
sized for how little data one person generates by dragging windows
around, not a claim of anything more sophisticated. When you grab an
app it has enough confident history on, it logs a suggestion to the
activity log ("Pattern: you usually drop 'Chrome' on the Right monitor
around this time (78% confidence)") -- **it only ever logs this as
information, it never moves anything on its own.**

**"Generate Usage Report"** builds a self-contained local HTML file
(`usage_report.html`) summarizing your most-moved apps, monitor usage,
busiest hours/days, and what usually goes where, and opens it. This is a
local file you can open or share yourself -- there's no email/network
integration here, "send if asked for" is fulfilled by handing you the
file, not by this app sending anything on its own.

## Calibration tutorial, zone-based grabbing, face security, custom gestures, drag animations

Unchanged from v6 -- see that folder's README for details on the
guided calibration wizard, zone-based grabbing, face enrollment,
recordable custom gestures (built-in/keyboard/mouse), and the
paper/portal drag animations with live HUD target highlighting.

## How it works

- `engine.py` -- threaded camera/MediaPipe/gesture loop; also runs the
  presence/display-power state machine, face-gating checks, and
  pattern-learning hooks on every grab/drop.
- `pattern_learning.py` -- usage logging, the incremental neural network,
  and HTML report generation.
- `greeting.py` -- the JARVIS-style fade-in/out HUD greeting overlay.
- `face_auth.py` -- face enrollment/recognition; `recognize()` now also
  returns the detected face's bounding box (used by presence detection
  and hand-near-face suppression, not just identity).
- `window_manager.py` -- monitor enumeration, window placement/z-order,
  reliable foreground-stealing, topmost-window lookup, and
  `set_monitor_power()` (bounded-timeout `SendMessageTimeout`, since a
  plain broadcast `SendMessage` can hang indefinitely on an unresponsive
  window).
- `drag_overlay.py` -- animation system (paper/portal follow-visuals,
  HUD highlight, name label, particle trail).
- `zone_calibration.py` -- learned per-user hand-to-monitor mapping.
- `gestures.py` / `gesture_templates.py` / `key_capture.py` -- gesture
  detection and custom-gesture recording.
- `actions.py` -- built-in + recorded keyboard/mouse action dispatch.
- `calibration.py` / `calibration_ui.py` -- tutorial step machine + wizard.
- `face_ui.py` -- face enrollment wizard window.
- `overlay.py` -- shared camera-frame annotation.
- `config_io.py` -- settings persistence.
- `gui_app.py` / `gesture_ui.py` / `launch_gui.pyw` -- Tkinter control panel.
- `main.py` -- CLI front end.
- `models/hand_landmarker.task` -- Google's official open-source hand
  landmark model (Apache 2.0).
