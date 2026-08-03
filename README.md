# Hand Window Dragger (v1)

Iron-Man-style window control: grab the focused window with a fist gesture
in front of your webcam, move your hand across the frame, open your hand
to drop the window onto the corresponding monitor.

Built on [MediaPipe HandLandmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)
(Google, open source) for hand tracking, OpenCV for camera capture, and
pywin32 for window placement. Windows only.

## Requirements

- Windows with a webcam
- Python 3.10+
- `pip install -r requirements.txt`

## Run it

**GUI (recommended):** double-click `launch_gui.pyw`, or run

```
pythonw launch_gui.pyw
```

The control panel has Start/Stop, a live preview with gesture overlay,
sensitivity sliders, mirror/maximize toggles, a camera picker, and an
activity log. Settings persist to `config.json`.

**CLI:**

```
python main.py
```

Controls: `q` quit, `m` toggle mirror, `x` toggle maximize-on-drop,
`c` cycle camera, `s` save config.

## How it works

1. `engine.py` runs the camera + MediaPipe loop on a background thread and
   reports frames/events via callbacks — both `main.py` (CLI) and
   `gui_app.py` (GUI) drive the same engine.
2. `gestures.py` detects a rotation-invariant "fist" (grab) from the 21
   hand landmarks MediaPipe returns per frame.
3. On grab, the engine captures whichever window was last focused
   (`window_manager.py`, via `pywin32`) — not its own preview window.
4. While held, your hand's horizontal position selects a monitor zone
   (left/center/right, mapped to your actual monitor layout via
   `EnumDisplayMonitors`).
5. On release, the window is moved (and optionally maximized) onto that
   monitor.

## Files

- `engine.py` — UI-agnostic tracking/gesture/window-move engine (threaded)
- `gestures.py` — landmark-based gesture classification
- `window_manager.py` — monitor enumeration + window placement (Win32)
- `overlay.py` — shared camera-frame annotation (landmarks, zone highlight)
- `config_io.py` — settings persistence
- `gui_app.py` / `launch_gui.pyw` — Tkinter control panel
- `main.py` — CLI front end
- `models/hand_landmarker.task` — Google's official open-source hand
  landmark model (Apache 2.0)
