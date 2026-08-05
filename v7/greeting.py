"""JARVIS-style greeting overlay: big text with a soft glow behind it,
holds briefly, then fades out. Shown when the display wakes because a
recognized face returned (see engine.py's presence-display feature).
Main-thread only.
"""

import os
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

HOLD_MS = 900        # how long it stays fully visible before fading
FADE_MS = 1300        # fade duration -- moderate, not instant, not sluggish
FADE_STEPS = 24

BG_COLOR = "#010102"
BG_COLOR_RGB = (1, 1, 2)
TEXT_COLOR = (238, 231, 221)  # warm off-white, not a bright cartoon color

# Dim clay -> warm clay, interpolated into many close steps so the glow
# reads as a smooth gradient rather than a handful of visible rings.
_GLOW_DIM = (18, 10, 7)
_GLOW_BRIGHT = (168, 92, 66)
_GLOW_BAND_COUNT = 14

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuisl.ttf",  # Segoe UI Semilight
    r"C:\Windows\Fonts\segoeuil.ttf",   # Segoe UI Light
    r"C:\Windows\Fonts\segoeui.ttf",    # Segoe UI Regular
]


def _load_font(size):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _render_greeting_image(name, font_size=50):
    text = f"Hi {name.title()}"
    font = _load_font(font_size)

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = probe.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad_x, pad_y = 120, 85
    w, h = int(text_w + pad_x * 2), int(text_h + pad_y * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = w / 2, h / 2

    # Banded glow: concentric ellipses drawn largest-and-dimmest first,
    # smallest-and-warmest last -- a stepped approximation of a soft
    # glow "orb" behind the text. Every pixel here is fully opaque or
    # fully transparent, never in between: Tk's -transparentcolor is a
    # hard binary color-key, not real alpha blending, so a true
    # Gaussian-blurred glow would leave a visible fringe where partially
    # transparent pixels get flattened onto the key color (the same bug
    # fixed earlier in the crushed-paper drag animation).
    base_rx, base_ry = text_w * 0.75, h * 0.42
    for i in range(_GLOW_BAND_COUNT, 0, -1):
        t = i / _GLOW_BAND_COUNT           # 1.0 (outermost) -> ~0.07 (innermost)
        mult = 0.55 + 0.95 * t              # outer band biggest, inner band smallest
        blend = 1.0 - t                     # 0 at the outer edge -> ~1 near the center
        color = tuple(int(_GLOW_DIM[c] + (_GLOW_BRIGHT[c] - _GLOW_DIM[c]) * blend) for c in range(3))
        rx, ry = base_rx * mult, base_ry * mult
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(*color, 255))

    tx = (w - text_w) / 2 - bbox[0]
    ty = (h - text_h) / 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=(*TEXT_COLOR, 255))

    return img


class JarvisGreeting:
    def __init__(self, root, center_x, center_y, name):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG_COLOR)
        try:
            self.win.attributes("-transparentcolor", BG_COLOR)
        except tk.TclError:
            pass

        pil_img = _render_greeting_image(name)
        flat = Image.new("RGB", pil_img.size, BG_COLOR_RGB)
        flat.paste(pil_img, mask=pil_img.split()[3])
        self._photo = ImageTk.PhotoImage(flat)

        label = tk.Label(self.win, image=self._photo, bg=BG_COLOR, bd=0, highlightthickness=0)
        label.pack()

        w, h = pil_img.size
        x = int(center_x - w / 2)
        y = int(center_y - h / 2)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        try:
            self.win.attributes("-alpha", 1.0)
        except tk.TclError:
            pass

        self._token = self.root.after(HOLD_MS, self._start_fade)

    def _start_fade(self):
        self._fade_step(FADE_STEPS)

    def _fade_step(self, remaining):
        if not self.win.winfo_exists():
            return
        alpha = remaining / FADE_STEPS
        try:
            self.win.attributes("-alpha", alpha)
        except tk.TclError:
            pass
        if remaining <= 0:
            self.destroy()
        else:
            self._token = self.root.after(int(FADE_MS / FADE_STEPS), lambda: self._fade_step(remaining - 1))

    def destroy(self):
        if self._token:
            try:
                self.root.after_cancel(self._token)
            except tk.TclError:
                pass
            self._token = None
        if self.win.winfo_exists():
            self.win.destroy()
