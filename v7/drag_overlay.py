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
- HUD highlight -- corner-bracket outline (Iron Man HUD style) around
  whichever window is topmost on the monitor the hand currently points
  at, updated live as the hand crosses zones.
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


_PORTAL_RING_COLORS = ["#5c3600", "#b36b00", "#ff9900", "#ffd24d"]
_PORTAL_RING_MULTS = [1.0, 0.8, 0.6, 0.38]
_PORTAL_ARC_COLOR = "#fff6cc"


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
        self._portal_items = None    # portal follow-visual (list of canvas items)
        self._portal_center = None
        self._portal_radius = 55
        self._portal_phase = 0
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

    def show_portal(self, center_x, center_y, radius=55):
        self._portal_center = (center_x - self.origin_x, center_y - self.origin_y)
        self._portal_radius = radius
        if self._portal_items is None:
            self._portal_items = []
            for color in _PORTAL_RING_COLORS:
                self._portal_items.append(self.canvas.create_oval(0, 0, 0, 0, fill=color, outline=""))
            for _ in range(3):
                self._portal_items.append(
                    self.canvas.create_arc(0, 0, 0, 0, style="arc", outline=_PORTAL_ARC_COLOR, width=3)
                )
            self._portal_tick()
        self._redraw_portal()

    def move_portal(self, center_x, center_y):
        if self._portal_items is None:
            return
        self._portal_center = (center_x - self.origin_x, center_y - self.origin_y)
        self._redraw_portal()

    def _redraw_portal(self):
        if self._portal_center is None or self._portal_items is None:
            return
        lx, ly = self._portal_center
        r = self._portal_radius
        for i, mult in enumerate(_PORTAL_RING_MULTS):
            rr = r * mult
            ry = rr * 0.42
            self.canvas.coords(self._portal_items[i], lx - rr, ly - ry, lx + rr, ly + ry)
        for j in range(3):
            item = self._portal_items[4 + j]
            rr = r * (0.5 + 0.18 * j)
            ry = rr * 0.42
            start = (self._portal_phase + j * 130) % 360
            self.canvas.coords(item, lx - rr, ly - ry, lx + rr, ly + ry)
            self.canvas.itemconfigure(item, start=start, extent=90)

    def _portal_tick(self):
        if self._portal_items is None:
            return
        self._portal_phase = (self._portal_phase + 8) % 360
        self._redraw_portal()
        self._portal_anim_token = self.root.after(45, self._portal_tick)

    def _clear_portal(self):
        if self._portal_items:
            for item in self._portal_items:
                self.canvas.delete(item)
            self._portal_items = None
        self._portal_center = None
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

    def show_hud_highlight(self, rect, color="#00e5ff", thickness=3, bracket_frac=0.16):
        """Corner-bracket outline (Iron Man HUD style) around rect."""
        l, t, r, b = self._to_local(rect)
        w, h = r - l, b - t
        bl = max(10, min(abs(w), abs(h)) * bracket_frac)
        corners = [
            (l, t + bl, l, t, l + bl, t),
            (r - bl, t, r, t, r, t + bl),
            (r, b - bl, r, b, r - bl, b),
            (l + bl, b, l, b, l, b - bl),
        ]
        if self._hud_items is None:
            self._hud_items = [
                self.canvas.create_line(*pts, fill=color, width=thickness, capstyle="round") for pts in corners
            ]
        else:
            for item, pts in zip(self._hud_items, corners):
                self.canvas.coords(item, *pts)
                self.canvas.itemconfigure(item, fill=color)

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

    def show_glow(self, rect, color="#00e5ff", thickness=5):
        l, t, r, b = self._to_local(rect)
        if self._rect_item is None:
            self._rect_item = self.canvas.create_rectangle(l, t, r, b, outline=color, width=thickness)
        else:
            self.canvas.coords(self._rect_item, l, t, r, b)
            self.canvas.itemconfigure(self._rect_item, outline=color, width=thickness)

    def animate_reveal(self, start_rect, end_rect, color="#00e5ff", interval_ms=13,
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
