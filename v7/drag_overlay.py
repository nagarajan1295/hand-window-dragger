"""Optional visual feedback while a window is being grabbed/dragged/dropped.

Everything here is driven from the GUI's main thread only (never the
engine's camera thread) and drawn into ONE borderless, always-on-top,
color-keyed Toplevel per drag, sized once to cover the whole area the
animation could possibly need (the virtual desktop bounds) and never
resized again -- every animation frame after that is a cheap Canvas item
move/recolor/redraw, not an OS-level window resize. An earlier version
called Toplevel.geometry() (a real window resize) on every animation
frame, which is what made it look janky.

Independent visual layers, combinable:

- Follow-visual (pick one): "paper" -- the grabbed window shrinks to a
  stylized crumpled-paper silhouette that follows the hand; "portal" --
  a swirling gold portal ring follows the hand instead. Either way, the
  real window has already moved instantly underneath (see
  window_manager.move_window_to_monitor); on drop, the visual grows into
  an outline at the real, final window rect ("reveal") so the transition
  doesn't look like a teleport.
- HUD highlight -- a solid outline framing whichever window is topmost
  on the monitor the hand currently points at, updated live as the hand
  crosses zones.
- Name label -- the grabbed window's title, shown above the follow-visual.
- Particle trail -- a short fading dot trail behind the follow-visual's
  motion.
"""

import ctypes
import math
import random
import tkinter as tk

import win32gui
import win32ui
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageTk

_PW_RENDERFULLCONTENT = 2

KEY_COLOR = "#ff00fe"
KEY_COLOR_RGB = (255, 0, 254)

# Shared warm accent (a muted clay/terracotta, not a bright cartoon
# color) used as the default for the HUD highlight and reveal outline.
THEME_COLOR = "#cc785c"


def capture_window_thumbnail(hwnd):
    """Grab a live bitmap of hwnd via PrintWindow. Returns a PIL RGB Image,
    or None if the window is gone or the capture fails (best-effort --
    this is decorative, never load-bearing)."""
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bitmap)
        # PW_RENDERFULLCONTENT -- needed for DWM-composited apps (browsers,
        # Electron, etc.) to not capture as blank/black. pywin32's win32gui
        # doesn't expose PrintWindow, so call user32 directly.
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT)
        bmp_info = bitmap.GetInfo()
        bmp_bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]), bmp_bits, "raw", "BGRX", 0, 1,
        )
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        if not result:
            return None
        return img
    except (win32gui.error, OSError):
        return None


def make_crumpled_icon(source_image, size=110, seed=None):
    """Shrink a window thumbnail into a stylized 'ball of crumpled paper':
    desaturated, clipped to a wobbly blob silhouette. Just the outline
    shape -- no crease-line texture drawn over it."""
    rng = random.Random(seed)
    img = source_image.convert("RGB").resize((size, size), Image.LANCZOS)
    img = ImageEnhance.Color(img).enhance(0.35)
    img = ImageEnhance.Brightness(img).enhance(0.92)
    img = ImageOps.autocontrast(img, cutoff=1)

    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    cx, cy = size / 2, size / 2
    base_r = size * 0.46
    n = 14
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        r = base_r * (0.8 + 0.2 * rng.random())
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    mdraw.polygon(points, fill=255)
    # Blur for a softer contour, then snap back to a hard 0/255 mask.
    # Tk's -transparentcolor is a binary color-key, not real alpha
    # blending: a soft (partially transparent) edge here would get
    # flattened into a color that's a blend of the artwork and the key
    # color, which then *doesn't* match the key color and shows up as a
    # visible fringe/halo around the shape instead of disappearing.
    mask = mask.filter(ImageFilter.GaussianBlur(2))
    mask = mask.point(lambda p: 255 if p >= 128 else 0)

    rgba = img.convert("RGBA")
    rgba.putalpha(mask)
    return rgba


def flatten_for_transparency(rgba_image, key_color=KEY_COLOR_RGB):
    """Composite an RGBA image onto a solid key color, for Tk's
    -transparentcolor color-keying (Tk has no true per-pixel alpha)."""
    bg = Image.new("RGB", rgba_image.size, key_color)
    bg.paste(rgba_image, mask=rgba_image.split()[3])
    return bg


def _ease_out_cubic(t):
    return 1 - (1 - t) ** 3


_PORTAL_SIZE = 132          # pixel diameter of the rendered portal image
_PORTAL_FRAME_COUNT = 24    # pre-rendered rotation steps, cycled while shown
_PORTAL_GLOW_DIM = (40, 22, 4)
_PORTAL_GLOW_BRIGHT = (255, 178, 64)
_PORTAL_GLOW_BANDS = 10


def _make_portal_frame(size, angle):
    """One frame of a swirling energy-portal disc at a given rotation
    (degrees) -- perfectly circular, banded/stepped shading rather than
    a blurred gradient (same color-key-safety reason as the crumpled-
    paper mask and the greeting glow: Tk's -transparentcolor is a hard
    binary key, and any softly blurred edge would flatten into a visible
    fringe instead of disappearing)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    outer_r = size / 2 - 2

    for i in range(_PORTAL_GLOW_BANDS, 0, -1):
        t = i / _PORTAL_GLOW_BANDS
        r = outer_r * (0.55 + 0.55 * t)
        blend = 1.0 - t
        color = tuple(int(_PORTAL_GLOW_DIM[c] + (_PORTAL_GLOW_BRIGHT[c] - _PORTAL_GLOW_DIM[c]) * blend)
                      for c in range(3))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, 255))

    ring_r = outer_r * 0.6
    draw.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                 outline=(255, 224, 140, 255), width=max(2, size // 26))

    void_r = ring_r * 0.86
    draw.ellipse([cx - void_r, cy - void_r, cx + void_r, cy + void_r], fill=(18, 9, 2, 255))

    for i in range(3):
        a0 = angle + i * 120
        streak_r = void_r * (0.5 + 0.22 * i)
        draw.arc([cx - streak_r, cy - streak_r, cx + streak_r, cy + streak_r],
                  start=a0, end=a0 + 65, fill=(255, 205, 110, 255), width=max(2, size // 32))

    return img


class DragOverlay:
    """A single borderless, always-on-top, color-keyed Toplevel covering
    `bounds`, created once per drag. All visual updates after that are
    Canvas item operations, never a window resize. Call from the Tkinter
    main thread only."""

    def __init__(self, root, bounds):
        self.root = root
        self.origin_x, self.origin_y = bounds[0], bounds[1]
        w = max(1, bounds[2] - bounds[0])
        h = max(1, bounds[3] - bounds[1])

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass  # non-Windows Tk build; overlay just won't be transparent
        self.win.geometry(f"{w}x{h}+{bounds[0]}+{bounds[1]}")

        self.canvas = tk.Canvas(self.win, width=w, height=h, bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._rect_item = None       # reveal-animation outline
        self._image_item = None      # paper follow-visual
        self._photo = None
        self._portal_item = None     # portal follow-visual (single image, frame-cycled)
        self._portal_frames = None
        self._portal_frame_idx = 0
        self._portal_anim_token = None
        self._hud_items = None       # live target-window highlight (4 corner brackets)
        self._label_item = None
        self._label_shadow_item = None
        self._trail_items = []
        self._trail_positions = []
        self._anim_token = None      # reveal-animation scheduler

    def _to_local(self, rect):
        l, t, r, b = rect
        return (l - self.origin_x, t - self.origin_y, r - self.origin_x, b - self.origin_y)

    # ---------- follow-visual: paper ----------

    def show_ball(self, rgba_image, center_x, center_y):
        flat = flatten_for_transparency(rgba_image)
        self._photo = ImageTk.PhotoImage(flat)
        lx, ly = center_x - self.origin_x, center_y - self.origin_y
        if self._image_item is None:
            self._image_item = self.canvas.create_image(lx, ly, image=self._photo)
        else:
            self.canvas.itemconfigure(self._image_item, image=self._photo)
            self.canvas.coords(self._image_item, lx, ly)

    def move_ball(self, center_x, center_y):
        if self._image_item is None:
            return
        self.canvas.coords(self._image_item, center_x - self.origin_x, center_y - self.origin_y)

    # ---------- follow-visual: portal ----------

    def show_portal(self, center_x, center_y, size=_PORTAL_SIZE):
        lx, ly = center_x - self.origin_x, center_y - self.origin_y
        if self._portal_item is None:
            self._portal_frames = []
            for i in range(_PORTAL_FRAME_COUNT):
                angle = i * (360 / _PORTAL_FRAME_COUNT)
                flat = flatten_for_transparency(_make_portal_frame(size, angle))
                self._portal_frames.append(ImageTk.PhotoImage(flat))
            self._portal_frame_idx = 0
            self._portal_item = self.canvas.create_image(lx, ly, image=self._portal_frames[0])
            self._portal_tick()
        else:
            self.canvas.coords(self._portal_item, lx, ly)

    def move_portal(self, center_x, center_y):
        if self._portal_item is None:
            return
        self.canvas.coords(self._portal_item, center_x - self.origin_x, center_y - self.origin_y)

    def _portal_tick(self):
        if self._portal_item is None:
            return
        self._portal_frame_idx = (self._portal_frame_idx + 1) % len(self._portal_frames)
        self.canvas.itemconfigure(self._portal_item, image=self._portal_frames[self._portal_frame_idx])
        self._portal_anim_token = self.root.after(55, self._portal_tick)

    def _clear_portal(self):
        if self._portal_item is not None:
            self.canvas.delete(self._portal_item)
            self._portal_item = None
        self._portal_frames = None
        if self._portal_anim_token:
            try:
                self.root.after_cancel(self._portal_anim_token)
            except tk.TclError:
                pass
            self._portal_anim_token = None

    def clear_follow_visual(self):
        if self._image_item is not None:
            self.canvas.delete(self._image_item)
            self._image_item = None
            self._photo = None
        self._clear_portal()

    # ---------- live HUD target-window highlight ----------

    def show_hud_highlight(self, rect, color=THEME_COLOR, thickness=6):
        """Solid rectangle outline framing whichever window the hand is
        currently pointing at -- a clearly visible full border, not thin
        corner accents (those read as "just a line" at a glance)."""
        l, t, r, b = self._to_local(rect)
        if self._hud_items is None:
            outer = self.canvas.create_rectangle(l, t, r, b, outline=color, width=thickness)
            self._hud_items = [outer]
        else:
            self.canvas.coords(self._hud_items[0], l, t, r, b)
            self.canvas.itemconfigure(self._hud_items[0], outline=color, width=thickness)

    def hide_hud_highlight(self):
        if self._hud_items:
            for item in self._hud_items:
                self.canvas.delete(item)
            self._hud_items = None

    # ---------- name label ----------

    def show_label(self, text, center_x, center_y, offset_y=-72):
        lx, ly = center_x - self.origin_x, center_y - self.origin_y + offset_y
        if self._label_item is None:
            self._label_shadow_item = self.canvas.create_text(
                lx + 1, ly + 1, text=text, fill="#000000", font=("Segoe UI", 11, "bold"))
            self._label_item = self.canvas.create_text(
                lx, ly, text=text, fill="#ffffff", font=("Segoe UI", 11, "bold"))
        else:
            self.canvas.coords(self._label_shadow_item, lx + 1, ly + 1)
            self.canvas.itemconfigure(self._label_shadow_item, text=text)
            self.canvas.coords(self._label_item, lx, ly)
            self.canvas.itemconfigure(self._label_item, text=text)

    def hide_label(self):
        if self._label_item is not None:
            self.canvas.delete(self._label_item)
            self.canvas.delete(self._label_shadow_item)
            self._label_item = None
            self._label_shadow_item = None

    # ---------- particle trail ----------

    def update_trail(self, center_x, center_y, color="#66d9ff", max_len=8):
        lx, ly = center_x - self.origin_x, center_y - self.origin_y
        self._trail_positions.append((lx, ly))
        if len(self._trail_positions) > max_len:
            self._trail_positions.pop(0)
        for item in self._trail_items:
            self.canvas.delete(item)
        self._trail_items = []
        n = len(self._trail_positions)
        for i, (px, py) in enumerate(self._trail_positions):
            frac = (i + 1) / n  # 0..1, newest (last appended) = 1
            radius = 2 + 5 * frac
            item = self.canvas.create_oval(px - radius, py - radius, px + radius, py + radius,
                                            fill=color, outline="")
            self.canvas.tag_lower(item)  # keep trail behind the follow-visual/HUD/label
            self._trail_items.append(item)

    def clear_trail(self):
        for item in self._trail_items:
            self.canvas.delete(item)
        self._trail_items = []
        self._trail_positions = []

    # ---------- reveal animation (drop transition) ----------

    def show_glow(self, rect, color=THEME_COLOR, thickness=5):
        l, t, r, b = self._to_local(rect)
        if self._rect_item is None:
            self._rect_item = self.canvas.create_rectangle(l, t, r, b, outline=color, width=thickness)
        else:
            self.canvas.coords(self._rect_item, l, t, r, b)
            self.canvas.itemconfigure(self._rect_item, outline=color, width=thickness)

    def animate_reveal(self, start_rect, end_rect, color=THEME_COLOR, interval_ms=13,
                        min_steps=12, max_steps=36, on_done=None):
        """Grow an outline from start_rect to end_rect (absolute screen
        coordinates) -- the window "materializing" at the drop point --
        then call on_done. Clears any active follow-visual/trail first.

        Step count scales with distance traveled: a fixed low step count
        made long cross-monitor drags look choppy (huge jumps per frame)
        while short ones looked fine -- about one step per 35px keeps
        per-frame motion visually consistent regardless of distance.
        """
        self.clear_follow_visual()
        self.clear_trail()
        self.show_glow(start_rect, color=color)

        scx, scy = (start_rect[0] + start_rect[2]) / 2, (start_rect[1] + start_rect[3]) / 2
        ecx, ecy = (end_rect[0] + end_rect[2]) / 2, (end_rect[1] + end_rect[3]) / 2
        distance = math.hypot(ecx - scx, ecy - scy)
        steps = int(max(min_steps, min(max_steps, distance / 35)))

        def _step(i):
            t = _ease_out_cubic(i / steps)
            rect = tuple(s + (e - s) * t for s, e in zip(start_rect, end_rect))
            self.show_glow(rect, color=color)
            if i < steps:
                self._anim_token = self.root.after(interval_ms, lambda: _step(i + 1))
            else:
                self._anim_token = None
                if on_done:
                    on_done()

        _step(0)

    def destroy(self):
        if self._anim_token:
            try:
                self.root.after_cancel(self._anim_token)
            except tk.TclError:
                pass
            self._anim_token = None
        if self._portal_anim_token:
            try:
                self.root.after_cancel(self._portal_anim_token)
            except tk.TclError:
                pass
            self._portal_anim_token = None
        self.win.destroy()
