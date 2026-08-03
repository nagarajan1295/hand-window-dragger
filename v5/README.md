# Hand Window Dragger (v5)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

Everything in [v4](../v4) (calibration tutorial, zone-based grabbing,
face-recognition security, fully customizable gestures, drag animations)
plus a round of fixes aimed specifically at *professional-feeling* drops
and animations: windows reliably land in front (not behind) whatever
else is on the target monitor, the crushed-paper animation has no more
color-fringe artifacts and scales its frame count to distance so long
cross-monitor drags don't look choppy, and the app now **learns your
personal hand-to-monitor mapping** from how you actually drop things
instead of assuming the camera frame splits evenly across monitors.

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

## What's new in v5

**Dropped windows reliably come to the front.** v4 already switched to
`HWND_TOP` for z-order, but `SetForegroundWindow` from a background
process is silently blocked by Windows' focus-stealing prevention --
which could still leave a drop looking like it landed behind something.
`window_manager.force_foreground()` now briefly attaches our input queue
to the current foreground thread (the standard workaround for this
restriction) before requesting foreground, which is a real, verified fix
-- not a hopeful retry.

**Crushed-paper animation has no more color fringe.** The wobbly paper
silhouette's edge was anti-aliased (Gaussian-blurred), but Tk's
`-transparentcolor` is a hard binary color-key, not real alpha blending
-- so semi-transparent edge pixels blended with the magenta key color
into something that *wasn't* the key color anymore, leaving a visible
magenta halo around the shape. The mask is now thresholded back to a
crisp 0/255 edge after blurring, so the contour stays soft-looking but
every pixel key-colors cleanly.

**Animation step count scales with distance.** A fixed low step count
made long cross-monitor drags look choppy (huge jumps per frame) while
short same-monitor ones looked fine. Steps now scale roughly one per
35px traveled (12-36 steps), so per-frame motion stays visually
consistent regardless of how far a drop travels.

**The crushed-paper ball follows your hand with cursor-like smoothing.**
Frame-to-frame hand-landmark jitter used to make the ball twitch; it now
eases toward the target position each frame instead of snapping to it.

**The app learns your personal hand-to-monitor mapping
(`zone_calibration.py`).** Instead of assuming your camera frame splits
evenly into thirds (or however many monitors you have), each monitor
zone has a learned "center" hand position, seeded by the calibration
tutorial's zone-confirmation steps and continuously refined by every
real drop you make afterward (a small nudge each time, so it keeps
adapting as your habits/setup change). Zone boundaries are the midpoints
between learned centers rather than a fixed even split -- this is
specifically aimed at "I drop where the monitor isn't, because the
camera's field of view doesn't intuitively line up with where my
monitors are." The crushed-paper ball's position uses the same learned
mapping, so what you see match what the grab logic actually does.
Stored in `zone_profile.json` (gitignored).

(For context: I also checked whether Windows animations/transparency/DWM
composition were disabled on this machine, since that was floated as a
possible cause -- they weren't; all three were on, with a discrete GPU
available. The rendering issues were bugs in this app's own code, now
fixed above.)

## Calibration tutorial

First run (or any time via **Run Calibration Tutorial**), the app walks
you through a step-by-step wizard, generated from how many monitors you
actually have connected:

1. Show your right hand, palm open
2. Show your left hand, palm open
3. Make a fist and hold it -- the **grab** gesture
4. Open your hand -- the **drop** gesture
5. For each monitor, left to right: grab, move your hand toward it, and
   drop -- confirming the zone mapping matches your physical layout, and
   (new in v5) feeding that exact hand position into the learned zone
   center for that monitor

From this the app learns:
- **Fist sensitivity** -- how many curled fingers count as "fist" for
  your hand, from what you actually did in step 3
- **Mirror orientation** -- if you drop on the wrong monitor twice during
  step 5, it flips the mirror setting automatically and has you retry
- **Per-monitor hand-position centers** -- see "What's new in v5" above

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

## Drag animations

Settings -> **Drag animation**: three options, **None** by default.
- **Glow + swish** -- a glowing outline frames the grabbed window while
  held, then slides/grows to the target monitor when you drop.
- **Crushed paper** -- the grabbed window shrinks into a stylized
  crumpled-paper icon that follows your hand across monitors as you move
  it (now with cursor-like smoothing and your calibrated zone mapping,
  see above), then grows back into shape when you drop.

Both are cosmetic overlays (`drag_overlay.py`): the real window still
moves instantly underneath, same as always -- these just animate a
stand-in on top so the transition doesn't look like a teleport. The
overlay window is created once per drag and never resized again; every
animation frame after that is a cheap Canvas coordinate update, not an
OS-level window resize (which is what made early versions look janky).

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

## How it works

- `engine.py` -- threaded camera/MediaPipe/gesture loop, callback-driven;
  both `main.py` and `gui_app.py` share it. Also runs the periodic
  face-recognition check, dispatches custom-gesture actions, and updates
  the learned zone profile after every drop.
- `zone_calibration.py` -- per-user learned hand-to-monitor mapping:
  running-average zone centers, midpoint boundaries, online updates
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
  reliable foreground-stealing (`force_foreground`), topmost-window-per-
  monitor lookup for zone-based grabbing, and virtual-desktop bounds
  (Win32)
- `drag_overlay.py` -- window thumbnail capture (`PrintWindow`), the
  crumpled-paper stylization (crisp color-keyed edges), and the
  fixed-size overlay window used for both animation styles
- `overlay.py` -- shared camera-frame annotation
- `config_io.py` -- settings persistence (defaults < calibration < saved
  config, in that precedence order)
- `gui_app.py` / `gesture_ui.py` / `launch_gui.pyw` -- Tkinter control panel
- `main.py` -- CLI front end
- `models/hand_landmarker.task` -- Google's official open-source hand
  landmark model (Apache 2.0)
