# Hand Window Dragger (v6)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

Everything in [v5](../v5) (calibration tutorial, zone-based grabbing,
face-recognition security, custom gestures, learned per-user hand-to-
monitor mapping) with the animation system reworked based on real
feedback: the "glow + swish" style is gone (it didn't work), the
crushed-paper look lost its crease-line texture (just the silhouette
now), and there's a new portal style plus several independent visual
layers you can combine freely.

Built on [MediaPipe HandLandmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)
(Google, open source) for hand tracking, OpenCV for camera capture and
face detection/recognition (Haar cascade + LBPH, via opencv-contrib), and
pywin32 for window placement and system actions. Windows only.

## Requirements

- Windows with a webcam
- Python 3.10+
- `pip install -r requirements.txt`

## Run it

Double-click `launch_gui.pyw`, or:

```
pythonw launch_gui.pyw
```

or the CLI:

```
python main.py
```

## Animation layers (v6)

Settings now split into a **single choice** (what the dragged window
looks like while it's in your hand -- it's one moving object, so it can
only look like one thing at a time) and **independent toggles** you can
combine with that choice and with each other freely:

**Follow visual** -- pick one:
- **None** (default)
- **Crushed paper** -- reworked: just the wobbly silhouette of a
  shrunk, desaturated window thumbnail now, no crease lines drawn over
  it (the lines were the #1 complaint about how this looked in v5)
- **Portal** -- new: a swirling gold/amber ring (concentric discs +
  rotating arcs, Rick-and-Morty-portal-ish) follows your hand instead of
  a paper ball. Purely procedural/vector-drawn, no window thumbnail
  involved -- the window "goes through" the portal conceptually rather
  than being shown shrunk down.

**Independent layers** (any combination, including with "None" above):
- **Highlight the window I'm pointing at** -- a corner-bracket HUD
  outline (Iron Man targeting-reticle style, not a plain box) around
  whichever window is currently topmost on the monitor your hand points
  at, live, updating as you cross monitors -- similar to how Claude
  highlights an element right before interacting with it. This is what
  replaces "glow + swish": instead of a static glow on the window you
  grabbed, it's a live preview of what you're about to drop onto.
- **Show window name while dragging** -- the grabbed window's title
  (e.g. "Microsoft Edge") shown as text above the paper/portal/wherever
  your hand is.
- **Particle trail** -- a short fading dot trail behind the follow-visual
  as it moves, for a bit of energy-trail flair.

All of these render into one overlay window created once per drag and
never resized -- every frame after that is a cheap Canvas item update,
not an OS-level window resize (see `drag_overlay.py`'s module docstring
for why that distinction mattered for smoothness).

### What got removed and why

**"Glow + swish"** is gone -- reported as not working at all. Rather
than debug a mode aiming to show "this is grabbed, now it slides to
there," the live HUD highlight above does a more useful version of the
same idea: it shows what you're *about to* drop onto, continuously,
which is arguably more informative than a slide animation ever was.

## Calibration tutorial

First run (or any time via **Run Calibration Tutorial**), the app walks
you through a step-by-step wizard, generated from how many monitors you
actually have connected: right hand, left hand, fist (grab), release
(drop), then one grab-move-drop rehearsal per monitor. From this it
learns your fist sensitivity, mirror orientation, and (as of v5) a
per-monitor hand-position mapping that keeps refining itself from every
real drop you make afterward. Results in `calibration.json` and
`zone_profile.json` (both gitignored -- specific to you).

## Zone-based grabbing

Toggle in settings: **"Grab whatever window is on your hand's monitor"**
(on by default). A grab targets whichever window is currently topmost on
the monitor your hand is positioned over, so re-grabbing the same spot
after moving something away automatically picks up whatever's now on
top there.

## Face-recognition security

**Face security** panel: enroll one or more people (a few seconds of
center/left/right head-turn samples each), then optionally require a
recognized face be in frame before any grab or custom gesture fires.
Removing your last enrolled face while that's on leaves everyone locked
out until you enroll someone new -- it never silently falls back to
unprotected. Not a security-grade biometric system; a convenience gate.

## Custom gestures

Record any hand pose (not a fist) for ~1.5 seconds and map it to a
built-in action (close/minimize window, lock screen, Alt+Tab), a
recorded keyboard shortcut, or a recorded mouse click at a captured
screen position.

## Quit

Dedicated **Quit** button, separate from Stop Tracking. Window is
resizable with a scrollable settings column so every control stays
reachable regardless of screen size.

## How it works

- `engine.py` -- threaded camera/MediaPipe/gesture loop, callback-driven;
  shared by `main.py` and `gui_app.py`. Also runs face-recognition
  checks, dispatches custom-gesture actions, and updates the learned
  zone profile after every drop.
- `zone_calibration.py` -- per-user learned hand-to-monitor mapping
- `drag_overlay.py` -- the animation system: window thumbnail capture,
  crumpled-paper stylization, procedural portal rendering, HUD
  corner-bracket highlighting, name labels, particle trails, and the
  fixed-size overlay window all of these render into
- `gestures.py` / `gesture_templates.py` / `key_capture.py` -- gesture
  detection, custom-gesture pose matching, keyboard-combo recording
- `actions.py` -- built-in + recorded keyboard/mouse action dispatch
- `calibration.py` / `calibration_ui.py` -- tutorial step machine + wizard
- `face_auth.py` / `face_ui.py` -- face enrollment (Haar cascade + LBPH)
- `window_manager.py` -- monitor enumeration, window placement/z-order,
  reliable foreground-stealing, topmost-window-per-monitor lookup
- `overlay.py` -- shared camera-frame annotation
- `config_io.py` -- settings persistence
- `gui_app.py` / `gesture_ui.py` / `launch_gui.pyw` -- Tkinter control panel
- `main.py` -- CLI front end
- `models/hand_landmarker.task` -- Google's official open-source hand
  landmark model (Apache 2.0)
