# Hand Window Dragger

Iron-Man-style window control: grab the focused window with a fist
gesture in front of your webcam, move your hand across the frame, open
your hand to drop the window onto the corresponding monitor. Built on
[MediaPipe HandLandmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)
(Google, open source), OpenCV, and pywin32. Windows only.

Five versions live side by side here as independent, runnable folders --
each is a complete, standalone app (own `requirements.txt`, own
`launch_gui.pyw`), not a diff against another version.

| Folder | What it is |
|---|---|
| [`v1/`](v1) | The original: fist grab/drop across monitors, Tkinter GUI + CLI. |
| [`v2/`](v2) | Adds the guided calibration tutorial, a real Quit control, and recordable custom gestures. |
| [`v3/`](v3) | Adds zone-based grabbing (grab whatever's on your hand's monitor), face-recognition security, keyboard/mouse custom-gesture actions, a resizable/scrollable window, and several tracking/drop-accuracy fixes. No animations. |
| [`v4/`](v4) | Same as v3, plus optional drag animations (glow+swish or crushed-paper), off by default. |
| [`v5/`](v5) | **Recommended.** Same as v4, plus: reliable foreground/z-order on drop (a real fix for windows landing behind others), no more color-fringe artifacts in the crushed-paper animation, distance-scaled animation smoothness, cursor-like ball-following, and a learned per-user hand-to-monitor mapping that adapts from how you actually drop things instead of assuming the camera frame splits evenly across monitors. |

v3/v4/v5 share the same tracking foundation -- each is strictly the
previous one plus fixes/features, not a different implementation. If a
later version ever regresses something, an earlier one is always there
as a fallback with the older, known-good behavior for that layer.

Pick a folder and follow its own README for setup and details.
