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


def virtual_desktop_bounds(monitors):
    """Bounding box (l, t, r, b) spanning every monitor -- used to map a
    hand's normalized camera position onto real screen coordinates."""
    lefts = [m["monitor"][0] for m in monitors]
    tops = [m["monitor"][1] for m in monitors]
    rights = [m["monitor"][2] for m in monitors]
    bottoms = [m["monitor"][3] for m in monitors]
    return min(lefts), min(tops), max(rights), max(bottoms)


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


def _rect_center(rect):
    l, t, r, b = rect
    return (l + r) // 2, (t + b) // 2


def _point_in_rect(pt, rect):
    x, y = pt
    l, t, r, b = rect
    return l <= x < r and t <= y < b


def get_topmost_window_on_monitor(monitor, exclude_pid=None):
    """Return the hwnd of the frontmost real window whose center sits on
    the given monitor, or None.

    EnumWindows hands back top-level windows in Z-order (topmost first),
    so the first real, non-excluded match whose center overlaps this
    monitor *is* "whatever's currently showing there" -- letting a grab
    target that window without the user needing to click it into OS
    focus first.

    The callback always returns True: pywin32's EnumWindows treats a
    False return (meant to just stop early) as an API failure and raises
    pywintypes.error, so matches are collected in full and the first
    (topmost) one is picked afterward instead.
    """
    found = []

    def _callback(hwnd, _extra):
        if not is_real_window(hwnd):
            return True
        if exclude_pid is not None:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == exclude_pid:
                return True
        rect = win32gui.GetWindowRect(hwnd)
        if _point_in_rect(_rect_center(rect), monitor["monitor"]):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(_callback, None)
    return found[0] if found else None


def force_foreground(hwnd):
    """Reliably bring hwnd to the front, including stealing foreground
    focus, from a background process.

    Plain SetForegroundWindow() is blocked by Windows' foreground-lock
    when the calling process isn't the one the user last interacted
    with -- which our engine thread never is, so it could silently fail,
    leaving the "dropped" window behind whatever was already on top.
    Briefly attaching our input queue to the current foreground window's
    thread satisfies the condition Windows checks, which is the standard
    workaround for this.
    """
    try:
        fg_hwnd = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

        attached_fg = attached_target = False
        if fg_thread and fg_thread != current_thread:
            win32process.AttachThreadInput(current_thread, fg_thread, True)
            attached_fg = True
        if target_thread and target_thread != current_thread:
            win32process.AttachThreadInput(current_thread, target_thread, True)
            attached_target = True
        try:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                                   win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached_fg:
                win32process.AttachThreadInput(current_thread, fg_thread, False)
            if attached_target:
                win32process.AttachThreadInput(current_thread, target_thread, False)
    except (win32gui.error, win32process.error):
        pass  # best-effort; the caller's own SetWindowPos already handled placement/size


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

    # HWND_TOP (not SWP_NOZORDER) so the window actually comes to the front
    # of whatever's already showing on the target monitor, instead of
    # landing behind it.
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOP, x, y, w, h,
        win32con.SWP_NOACTIVATE,
    )

    if maximize:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)

    force_foreground(hwnd)

    return True
