# Hand Window Dragger (v3)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

This is the "everything solid, nothing decorative" version: guided
calibration, zone-based grabbing, face-recognition security, and fully
customizable gestures (including recorded keyboard shortcuts and mouse
clicks) -- deliberately **without** the drag animations in
[v4](../v4), which target the same tracking/drop precision plus that
extra visual layer. If you want the most accurate, no-frills experience,
use this one.

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

## Calibration tutorial

First run (or any time via **Run Calibration Tutorial**), the app walks
you through a step-by-step wizard, generated from how many monitors you
actually have connected:

1. Show your right hand, palm open
2. Show your left hand, palm open
3. Make a fist and hold it -- the **grab** gesture
4. Open your hand -- the **drop** gesture
5. For each monitor, left to right: grab, move your hand toward it, and
   drop -- confirming the zone mapping matches your physical layout

From this the app learns:
- **Fist sensitivity** -- how many curled fingers count as "fist" for
  your hand, from what you actually did in step 3
- **Mirror orientation** -- if you drop on the wrong monitor twice during
  step 5, it flips the mirror setting automatically and has you retry

Results are saved to `calibration.json` (gitignored -- it's specific to
you). Skip or redo it anytime; sensible defaults apply either way.

## Zone-based grabbing

Toggle in settings: **"Grab whatever window is on your hand's monitor"**
(on by default). With it on, a grab targets whichever window is currently
topmost on the monitor your hand is positioned over -- so if you drop
File Explorer on the center monitor, then grab from the left monitor
again, you automatically get whatever's now on top there (say, a
browser) without needing to click it into focus first.

## Quit

The GUI has a dedicated **Quit** button that stops recognition, releases
the camera, and exits -- separate from Stop Tracking (which just pauses).
The CLI stops the same way with `q`. The window is resizable and the
settings column scrolls, so Quit and every other control stay reachable
regardless of screen height.

## Face-recognition security

**Face security** panel: **Enroll New Person...** captures your face from
a few angles (look center, left, right; each prompt gives you a "get
ready" countdown plus a ~3.5s capture window) and trains a recognizer
(`cv2.face` LBPH). Enroll more than one person if you want several people
authorized. Toggle **"Only respond to gestures from an enrolled face"** to
require a recognized face be in frame before any grab or custom gesture
fires. **Remove Selected** actually retrains the recognizer without that
person's data -- if you remove your only enrolled face while the toggle
is on, gestures stay locked for everyone until you enroll a new one
(it will not silently fall back to unprotected). Enrollment data lives in
`face_data/` (gitignored -- it's your face).

This is a convenience gate built on a lightweight, decades-old face
recognizer, not a security-grade biometric system -- don't rely on it to
keep out a determined adversary.

## Custom gestures

**Custom gestures** panel -> **Add New...**: name it, choose an action
type, then hold a distinct pose (anything but a fist, which stays
reserved for grabbing) for ~1.5 seconds. The app records a
scale-normalized feature vector from your landmarks and matches live
poses against it by nearest distance -- template matching on top of
MediaPipe's landmarks, not a separately trained classifier, so it's
instant to add or delete gestures.

Three action types:
- **Built-in** -- close current window, minimize current window, lock
  screen, switch to next window (Alt+Tab)
- **Keyboard shortcut** -- click the recording box and press any key
  combo (e.g. Ctrl+Shift+Esc); it's replayed via simulated key events
- **Mouse click** -- position your cursor where you want the click (a
  countdown gives you time to move it; nothing is clicked during
  recording), then it's replayed as a real click at that screen position
  when the gesture fires

Templates are saved to `gesture_templates.json` (gitignored).

## Notes on tracking accuracy

- Camera opens at its **driver default resolution** -- an earlier
  attempt to force 1920x1080 for a wider field of view actually made
  tracking and drop accuracy *worse* on this hardware (narrower
  effective FOV, softer preview text), so it isn't done here.
- **If your hand exits the frame near the left/right edge while holding
  a grab**, that's treated as intentionally dragging off that side and
  the window drops onto the corresponding monitor, instead of just
  cancelling the grab. Losing tracking while your hand was more central
  still cancels (presumed a tracking hiccup, not an intentional exit).
- **Dropped windows come to the front** of whatever else is on the
  target monitor (an earlier version had a real bug here: the move
  preserved z-order, so a drop could land behind an existing window).

## How it works

- `engine.py` -- threaded camera/MediaPipe/gesture loop, callback-driven;
  both `main.py` and `gui_app.py` share it. Also runs the periodic
  face-recognition check and dispatches custom-gesture actions.
- `gestures.py` -- rotation-invariant fist/curl detection
- `gesture_templates.py` -- custom gesture pose recording + nearest-
  neighbor matching
- `key_capture.py` -- Tkinter keysym -> Windows virtual-key mapping and
  modifier-tracking combo recorder, for the keyboard-shortcut action type
- `actions.py` -- built-in actions plus replaying recorded keyboard/mouse
  actions
- `calibration.py` / `calibration_ui.py` -- the tutorial's step machine
  and Tkinter wizard window
- `face_auth.py` / `face_ui.py` -- face enrollment (Haar cascade + LBPH)
  and its Tkinter wizard window
- `window_manager.py` -- monitor enumeration, window placement/z-order,
  and topmost-window-per-monitor lookup for zone-based grabbing (Win32)
- `overlay.py` -- shared camera-frame annotation
- `config_io.py` -- settings persistence (defaults < calibration < saved
  config, in that precedence order)
- `gui_app.py` / `gesture_ui.py` / `launch_gui.pyw` -- Tkinter control panel
- `main.py` -- CLI front end
- `models/hand_landmarker.task` -- Google's official open-source hand
  landmark model (Apache 2.0)
