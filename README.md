# Hand Window Dragger

Iron-Man-style window control: grab the focused window with a fist
gesture in front of your webcam, move your hand across the frame, open
your hand to drop the window onto the corresponding monitor. Built on
[MediaPipe HandLandmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)
(Google, open source), OpenCV, and pywin32. Windows only.

Four versions live side by side here as independent, runnable folders --
each is a complete, standalone app (own `requirements.txt`, own
`launch_gui.pyw`), not a diff against another version.

| Folder | What it is |
|---|---|
| [`v1/`](v1) | The original: fist grab/drop across monitors, Tkinter GUI + CLI. |
| [`v2/`](v2) | Adds the guided calibration tutorial, a real Quit control, and recordable custom gestures. |
| [`v3/`](v3) | **Recommended for reliability.** Adds zone-based grabbing (grab whatever's on your hand's monitor), face-recognition security, keyboard/mouse custom-gesture actions, a resizable/scrollable window, and several tracking/drop-accuracy fixes. No animations. |
| [`v4/`](v4) | Same as v3, plus optional drag animations (glow+swish or crushed-paper), off by default. |

v3 and v4 share the same tracking and drop-accuracy fixes -- v4 is
strictly v3 plus a cosmetic animation layer, not a different tracking
implementation. If v4's animations ever regress accuracy again, v3 is
the fallback with identical grab/drop behavior.

Pick a folder and follow its own README for setup and details.
