# Hand Window Dragger (v7)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

## Just want to try it?

Download the standalone Windows build from the
[Releases page](../../releases) -- no Python, no `pip install`, just
unzip and run `HandWindowDragger_v7.exe`. Everything below is for
running from source / hacking on it instead.

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
you for the configured **away threshold** (default 20s), it blanks the
screen with a plain black window covering every monitor -- not lock,
not system sleep, and deliberately *not* a real monitor power-off
either: an earlier version used the actual `SC_MONITORPOWER` call, but
changing real display power state is itself what makes Windows apply
its "require sign-in" lock policy the instant it happens, independent
of any idle timer. A software blackout never touches real display
power, so that policy never fires. The camera and app keep running
underneath the whole time, so it notices the moment you're back and
clears the blackout. Press **Escape** any time to dismiss it early
without waiting on face detection. Requires at least one enrolled face
(the toggle refuses to turn on otherwise -- without a recognized
identity to watch for, the screen would blank once and never find a
reason to clear).

**"Show a greeting when the display turns back on"** shows a greeting
overlay, holds briefly, then fades out (`greeting.py`) -- only fires
alongside the display turning back on from a recognized face, not on
every random face-detection blip. Fully customizable via **"Customize
Greeting..."**: your own text (`{name}` is an optional placeholder),
font, text/background colors, background shape (glow orb, rounded
rectangle, plain rectangle, or none), or swap in your own image instead
of text entirely -- with a live preview before saving.

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
- `greeting.py` / `greeting_ui.py` -- the fade-in/out greeting overlay
  and its settings dialog (custom text/font/colors/shape or a
  user-supplied image, with a live preview).
- `face_auth.py` -- face enrollment/recognition; `recognize()` now also
  returns the detected face's bounding box (used by presence detection
  and hand-near-face suppression, not just identity).
- `window_manager.py` -- monitor enumeration, window placement/z-order,
  reliable foreground-stealing, topmost-window lookup, and the
  idle-lock-suppression helpers used while the presence blackout is up.
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
