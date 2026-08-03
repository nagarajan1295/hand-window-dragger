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


def fire_keyboard(params):
    """Replay a recorded key combo. params['vk_codes'] is modifiers first,
    then the trigger key, e.g. [VK_CONTROL, VK_SHIFT, VK_ESCAPE]."""
    vks = params["vk_codes"]
    for vk in vks:
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.01)
    for vk in reversed(vks):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)


_MOUSE_EVENTS = {
    "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
    "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
}


def fire_mouse_click(params):
    """Replay a recorded click at a fixed screen position."""
    x, y = params["x"], params["y"]
    win32api.SetCursorPos((x, y))
    down, up = _MOUSE_EVENTS.get(params.get("button", "left"), _MOUSE_EVENTS["left"])
    for _ in range(params.get("clicks", 1)):
        win32api.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.02)
        win32api.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.05)


def fire_action(template):
    """Dispatch a gesture template's mapped action: built-in, a recorded
    keyboard combo, or a recorded mouse click."""
    action_type = template.get("action_type", "builtin")
    if action_type == "builtin":
        entry = ACTIONS.get(template["action"])
        if entry:
            entry[1]()
    elif action_type == "keyboard":
        fire_keyboard(template["action_params"])
    elif action_type == "mouse_click":
        fire_mouse_click(template["action_params"])
    else:
        raise ValueError(f"Unknown action_type: {action_type}")


def action_label(template):
    """Human-readable description of what a gesture template will do."""
    action_type = template.get("action_type", "builtin")
    if action_type == "builtin":
        entry = ACTIONS.get(template["action"])
        return entry[0] if entry else template["action"]
    if action_type == "keyboard":
        return f"Press {template['action_params'].get('display', '?')}"
    if action_type == "mouse_click":
        p = template["action_params"]
        return f"{p.get('button', 'left').title()} click at ({p['x']}, {p['y']})"
    return "Unknown action"
