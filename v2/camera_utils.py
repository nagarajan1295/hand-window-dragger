"""Shared camera-open helper + model path, split out from engine.py so
gesture_templates.py and calibration.py can use them without a circular
import back into engine.py."""

import os

import cv2

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "hand_landmarker.task")


def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    return cap
