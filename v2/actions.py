"""Built-in system/window actions that a custom gesture can be mapped to."""

import ctypes
import time

import win32api
import win32con
import win32gui


def close_active_window():
    hwnd = win32gui.GetForegroundWindow()
    if hwnd:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def minimize_active_window():
    hwnd = win32gui.GetForegroundWindow()
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)


def lock_screen():
    ctypes.windll.user32.LockWorkStation()


def alt_tab_next():
    """Simulate a quick Alt+Tab tap to switch to the previously used window."""
    VK_MENU, VK_TAB = 0x12, 0x09
    win32api.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.03)
    win32api.keybd_event(VK_TAB, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(VK_TAB, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)
    win32api.keybd_event(VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)


# key -> (display label, callable)
ACTIONS = {
    "close_window": ("Close current window", close_active_window),
    "minimize_window": ("Minimize current window", minimize_active_window),
    "lock_screen": ("Lock screen", lock_screen),
    "alt_tab": ("Switch to next window (Alt+Tab)", alt_tab_next),
}
