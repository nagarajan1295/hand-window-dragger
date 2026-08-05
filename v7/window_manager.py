"""Monitor enumeration and window move/maximize helpers (Win32)."""

import ctypes

import win32api
import win32con
import win32gui
import win32process

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def prevent_system_idle_lock(prevent):
    """Stop Windows' own inactivity timer from sleeping/locking the
    session while we're managing presence-based display power ourselves.

    Without this, Windows' own idle timeout can independently trigger
    "require sign-in" on the next screen wake -- including the wake WE
    cause by calling set_monitor_power(True) -- so the user comes back,
    the display turns on, and they're staring at the Windows lock screen
    instead of their desktop. Worse, while the session is locked, Windows
    blocks camera access for background apps, so the face-detection loop
    that's supposed to notice they're back never gets a frame, and
    set_monitor_power(True) never even fires -- it looks like the
    feature just doesn't work.

    Deliberately does NOT pass ES_DISPLAY_REQUIRED: that would force the
    display to always stay on and defeat the point. This only tells
    Windows "don't treat this as system-idle," so its own separate
    sleep/lock timer never fires; set_monitor_power() remains the only
    thing that blanks the screen. Reversible, no admin rights, no system
    settings touched -- must be called with prevent=False when this
    feature is disabled or the engine stops, to restore normal behavior.
    """
    flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED if prevent else _ES_CONTINUOUS
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


def simulate_trivial_input():
    """Nudge Windows' input-idle timer without visibly moving the mouse.

    prevent_system_idle_lock() above only stops the power-management idle
    timer. It has no effect on the screensaver's own idle timer (or a
    "machine inactivity limit" policy, if one is configured) -- those are
    driven by real keyboard/mouse activity via GetLastInputInfo, a
    completely separate mechanism. If the screensaver is set to "on
    resume, display logon screen" (the Windows default), it will still
    lock the session on its own schedule even while system-idle is
    suppressed. A zero-delta relative mouse move registers as input and
    resets that timer without actually moving the cursor.
    """
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, 0, 0, 0)


def set_monitor_power(on):
    """Turn the display(s) on or off -- not lock, not system sleep -- via
    the standard WM_SYSCOMMAND/SC_MONITORPOWER broadcast. The camera and
    this app keep running underneath; only the physical display power
    state changes.

    Uses SendMessageTimeout (not plain SendMessage): a broadcast to
    HWND_BROADCAST blocks waiting for every top-level window to process
    it, and one unresponsive window can hang the caller indefinitely --
    a bounded timeout with SMTO_ABORTIFHUNG keeps this from ever
    stalling the engine loop.
    """
    value = -1 if on else 2  # -1 = on, 2 = off (1 = low-power, unused here)
    try:
        win32gui.SendMessageTimeout(
            win32con.HWND_BROADCAST, win32con.WM_SYSCOMMAND, win32con.SC_MONITORPOWER, value,
            win32con.SMTO_ABORTIFHUNG, 1000,
        )
    except win32gui.error:
        pass


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


def monitor_index_for_window(hwnd, monitors):
    """Index (into the given left-to-right monitor list) of whichever
    monitor contains hwnd's center, or None."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    rect = win32gui.GetWindowRect(hwnd)
    center = _rect_center(rect)
    for i, m in enumerate(monitors):
        if _point_in_rect(center, m["monitor"]):
            return i
    return None


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
