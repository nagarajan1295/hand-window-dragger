"""Monitor enumeration and window move/maximize helpers (Win32)."""

import win32api
import win32con
import win32gui
import win32process


def get_monitors_sorted():
    """Return monitors left-to-right by their work-area X position.

    Each entry: {'device': str, 'monitor': (l,t,r,b), 'work': (l,t,r,b)}
    """
    monitors = []
    for hMonitor, _hdc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(hMonitor)
        monitors.append({
            "device": info["Device"],
            "monitor": info["Monitor"],
            "work": info["Work"],
        })
    monitors.sort(key=lambda m: m["work"][0])
    return monitors


def get_window_owner_pid(hwnd):
    if not hwnd:
        return None
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except win32gui.error:
        return None


def is_real_window(hwnd):
    """Filter out the shell, our own overlay, and windows without a title."""
    if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return False
    if not win32gui.GetWindowText(hwnd):
        return False
    class_name = win32gui.GetClassName(hwnd)
    if class_name in ("Shell_TrayWnd", "Progman", "WorkerW"):
        return False
    # Skip tool windows (they have no taskbar presence, e.g. tooltips).
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if ex_style & win32con.WS_EX_TOOLWINDOW:
        return False
    return True


def move_window_to_monitor(hwnd, monitor, maximize=True):
    """Move (and optionally maximize) hwnd onto the given monitor dict."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False

    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    elif placement[1] == win32con.SW_SHOWMINIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t

    work_l, work_t, work_r, work_b = monitor["work"]
    mw, mh = work_r - work_l, work_b - work_t

    w = min(w, int(mw * 0.9)) or mw
    h = min(h, int(mh * 0.9)) or mh
    x = work_l + (mw - w) // 2
    y = work_t + (mh - h) // 2

    win32gui.SetWindowPos(
        hwnd, None, x, y, w, h,
        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
    )

    if maximize:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)

    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass  # Windows may refuse focus theft from a background process; the move still applies.

    return True
