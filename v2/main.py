"""CLI front end for the hand-tracking window dragger.

Make a fist over the webcam to "grab" whatever window is currently
focused, move your hand left/center/right, open your hand to drop the
window onto the corresponding monitor.

For a point-and-click control panel instead, run gui_app.py.
"""
# Quit: press 'q'. This stops gesture recognition and releases the camera
# before the process exits (see the `finally` block in main() below).

import time

import cv2

from config_io import load_config, save_config
from engine import HandDraggerEngine
from overlay import draw_landmarks, draw_overlay

WINDOW_NAME = "Hand Window Dragger"


def main():
    cfg = load_config()

    def on_event(msg):
        print(msg)

    engine = HandDraggerEngine(config=cfg, on_event=on_event)

    print(f"Detected {len(engine.monitors)} monitor(s), left-to-right:")
    for m in engine.monitors:
        print(f"  {m['device']}  work={m['work']}")
    print("Controls: q=quit  m=toggle mirror  x=toggle maximize-on-drop  c=next camera  s=save config")

    latest = {}

    def on_frame(frame, info):
        if info["landmarks"] is not None:
            draw_landmarks(frame, info["landmarks"])
        draw_overlay(
            frame, info["monitors"], info["zone"], info["state"],
            info["grabbed_title"], info["fist_active"], info["hand_present"],
            info["active_gesture_name"], info["gesture_hold"], info["gesture_hold_needed"],
        )
        cv2.imshow(WINDOW_NAME, frame)
        latest["key"] = cv2.waitKey(1) & 0xFF

    engine.on_frame = on_frame
    engine.start()

    try:
        while engine.running:
            key = latest.get("key", 0xFF)
            latest["key"] = 0xFF
            if key == ord("q"):
                break
            elif key == ord("m"):
                cfg["mirror"] = not cfg["mirror"]
                print(f"mirror = {cfg['mirror']}")
            elif key == ord("x"):
                cfg["maximize_on_drop"] = not cfg["maximize_on_drop"]
                print(f"maximize_on_drop = {cfg['maximize_on_drop']}")
            elif key == ord("c"):
                engine.cycle_camera()
                print(f"camera_index = {cfg['camera_index']}")
            elif key == ord("s"):
                save_config(cfg)
                print(f"Saved config to disk")
            time.sleep(0.01)
    finally:
        engine.stop()
        save_config(cfg)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
