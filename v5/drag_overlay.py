"""Optional visual feedback while a window is being grabbed/dragged/dropped.

Two styles, driven entirely from the GUI's main thread (never the
engine's camera thread):

- "glow": a colored outline frames the grabbed window while held, then
  slides/grows to the target monitor's rect when dropped (the "swish").
- "paper": the grabbed window shrinks to a stylized crumpled-paper icon
  that follows the hand across monitors, then grows back into an outline
  at the target rect when dropped ("unfolding").

Both are cosmetic overlays layered on top of an already-instant real
window move -- the actual window snaps to its new spot immediately (see
window_manager.move_window_to_monitor); the overlay just animates a
stand-in on top of that so the transition doesn't look like a teleport.

The overlay window itself is created ONCE per drag, sized to cover the
whole area the animation could possibly need (the virtual desktop
bounds), and never resized again -- every animation frame after that is
a cheap Canvas item move/recolor, not an OS-level window resize. An
earlier version called Toplevel.geometry() (a real window resize) on
every single animation frame, which is what made it look janky.
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
    desaturated, clipped to a wobbly blob silhouette, with a few crease
    lines. A stylization, not a physics simulation."""
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

    creased = img.copy()
    cdraw = ImageDraw.Draw(creased)
    for _ in range(7):
        x1, y1 = rng.uniform(0, size), rng.uniform(0, size)
        x2, y2 = rng.uniform(0, size), rng.uniform(0, size)
        shade = rng.randint(40, 110)
        cdraw.line([(x1, y1), (x2, y2)], fill=(shade, shade, shade), width=rng.randint(1, 2))

    rgba = creased.convert("RGBA")
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


class DragOverlay:
    """A single borderless, always-on-top, color-keyed Toplevel covering
    `bounds`, created once per drag. All visual updates after that are
    Canvas item operations (coords/itemconfigure), never a window resize
    -- that's what keeps the animation smooth. Call from the Tkinter main
    thread only."""

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

        self._rect_item = None
        self._image_item = None
        self._photo = None
        self._anim_token = None

    def _to_local(self, rect):
        l, t, r, b = rect
        return (l - self.origin_x, t - self.origin_y, r - self.origin_x, b - self.origin_y)

    def show_glow(self, rect, color="#00e5ff", thickness=5):
        """Show/reposition a hollow colored outline at rect (absolute
        screen coordinates)."""
        l, t, r, b = self._to_local(rect)
        if self._rect_item is None:
            self._rect_item = self.canvas.create_rectangle(l, t, r, b, outline=color, width=thickness)
        else:
            self.canvas.coords(self._rect_item, l, t, r, b)
            self.canvas.itemconfigure(self._rect_item, outline=color, width=thickness)

    def show_ball(self, rgba_image, center_x, center_y):
        """Show/reposition the crumpled-paper icon centered at an absolute
        screen point."""
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

    def animate_swish(self, start_rect, end_rect, color="#00e5ff", interval_ms=13,
                       min_steps=12, max_steps=36, on_done=None):
        """Grow/slide a glow outline from start_rect to end_rect (absolute
        screen coordinates), then call on_done. Used both for the plain
        'swish' style's drop transition and for the paper style's
        unfold-at-drop (which first drops the ball image in favor of the
        outline).

        Step count scales with how far it travels: a fixed low step count
        made long cross-monitor drags look choppy (each frame jumping
        hundreds of pixels) while short ones looked fine -- about one
        step per 35px keeps per-frame motion visually consistent instead.
        """
        if self._image_item is not None:
            self.canvas.delete(self._image_item)
            self._image_item = None
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
        self.win.destroy()
