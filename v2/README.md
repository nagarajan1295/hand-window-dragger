# Hand Window Dragger (v2)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

v2 adds a guided, Face-ID-style **calibration tutorial**, a proper **Quit**
control, and **custom gestures** you can record and map to actions like
closing a window, locking the screen, or Alt-Tabbing. See v1 for the base
grab/drag/drop mechanic this builds on.

Built on [MediaPipe HandLandmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)
(Google, open source) for hand tracking, OpenCV for camera capture, and
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

## Quit

The GUI has a dedicated **Quit** button that stops recognition, releases
the camera, and exits -- separate from Stop Tracking (which just pauses).
The CLI stops the same way with `q`.

## Custom gestures

**Custom gestures** panel -> **Add New...**: name it, pick an action, hold
a distinct pose (anything but a fist, which stays reserved for grabbing)
for ~1.5 seconds. The app records a scale-normalized feature vector from
your landmarks and matches live poses against it by nearest distance --
template matching on top of MediaPipe's landmarks, not a separately
trained classifier, so it's instant to add or delete gestures.

Built-in actions (`actions.py`): close current window, minimize current
window, lock screen, switch to next window (Alt+Tab). Templates are saved
to `gesture_templates.json` (gitignored).

## How it works

- `engine.py` -- threaded camera/MediaPipe/gesture loop, callback-driven;
  both `main.py` and `gui_app.py` share it
- `gestures.py` -- rotation-invariant fist/curl detection
- `gesture_templates.py` -- custom gesture recording + nearest-neighbor
  matching
- `calibration.py` / `calibration_ui.py` -- the tutorial's step machine
  and Tkinter wizard window
- `actions.py` -- window/system actions custom gestures can trigger
- `window_manager.py` -- monitor enumeration + window placement (Win32)
- `overlay.py` -- shared camera-frame annotation
- `config_io.py` -- settings persistence (defaults < calibration < saved
  config, in that precedence order)
- `gui_app.py` / `gesture_ui.py` / `launch_gui.pyw` -- Tkinter control panel
- `main.py` -- CLI front end
- `models/hand_landmarker.task` -- Google's official open-source hand
  landmark model (Apache 2.0)
