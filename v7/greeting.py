"""JARVIS-style greeting overlay: big text (or a custom image) with a
soft glow behind it, holds briefly, then fades out. Shown when the
display wakes because a recognized face returned (see engine.py's
presence-display feature). Fully customizable from the settings app
(greeting_ui.py) -- text, font, colors, background shape, or a
user-supplied image instead of text entirely. Main-thread only.
"""

import os
import shutil
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

HOLD_MS = 900        # how long it stays fully visible before fading
FADE_MS = 1300        # fade duration -- moderate, not instant, not sluggish
FADE_STEPS = 24

BG_COLOR = "#010102"
BG_COLOR_RGB = (1, 1, 2)

# Fallback defaults, used whenever the user hasn't customized a setting
# yet (fresh config) or a stored value is invalid.
DEFAULT_TEXT = "Hi {name}"
DEFAULT_TEXT_COLOR = "#eee7dd"       # warm off-white, not a bright cartoon color
DEFAULT_BG_COLOR = "#a85c42"          # warm clay
DEFAULT_BG_SHAPE = "glow_orb"
DEFAULT_FONT = "Claude-style (Segoe UI Semilight)"

_GLOW_BAND_COUNT = 14

# Display name -> ordered fallback chain of font file paths. First
# existing, loadable file wins; falls back to Pillow's built-in font if
# none of a chain's paths exist.
FONT_CHOICES = {
    "Claude-style (Segoe UI Semilight)": [
        r"C:\Windows\Fonts\segoeuisl.ttf",
        r"C:\Windows\Fonts\segoeuil.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ],
    "Segoe UI": [r"C:\Windows\Fonts\segoeui.ttf"],
    "Segoe UI Bold": [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf"],
    "Consolas": [r"C:\Windows\Fonts\consola.ttf"],
    "Arial": [r"C:\Windows\Fonts\arial.ttf"],
    "Arial Bold": [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"],
    "Georgia": [r"C:\Windows\Fonts\georgia.ttf"],
    "Impact": [r"C:\Windows\Fonts\impact.ttf"],
    "Times New Roman": [r"C:\Windows\Fonts\times.ttf"],
}

BG_SHAPE_CHOICES = {
    "Glow Orb": "glow_orb",
    "Rounded Rectangle": "rounded_rect",
    "Rectangle": "rectangle",
    "None (text only)": "none",
}

GREETING_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "greeting_assets")
_MAX_IMAGE_DIM = 480  # cap so a huge photo doesn't swallow the screen


def save_greeting_image(src_path):
    """Copy a user-chosen image into the app's local assets folder so it
    persists independent of where the original file lives, and return
    the new path to store in config."""
    os.makedirs(GREETING_ASSETS_DIR, exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower() or ".png"
    dest = os.path.join(GREETING_ASSETS_DIR, f"custom_greeting{ext}")
    shutil.copyfile(src_path, dest)
    return dest


def _hex_to_rgb(hex_color, default):
    try:
        h = hex_color.lstrip("#")
        if len(h) != 6:
            raise ValueError
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError, TypeError):
        return default


def _load_font_by_name(name, size):
    for path in FONT_CHOICES.get(name, FONT_CHOICES[DEFAULT_FONT]):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _render_text_greeting(cfg, name, font_size=50):
    template = cfg.get("greeting_text") or DEFAULT_TEXT
    try:
        text = template.format(name=name.title())
    except (KeyError, IndexError):
        text = template  # user text has no {name} placeholder (or is malformed) -- show literally

    font = _load_font_by_name(cfg.get("greeting_font", DEFAULT_FONT), font_size)
    text_color = _hex_to_rgb(cfg.get("greeting_text_color"), _hex_to_rgb(DEFAULT_TEXT_COLOR, (238, 231, 221)))
    bg_color = _hex_to_rgb(cfg.get("greeting_bg_color"), _hex_to_rgb(DEFAULT_BG_COLOR, (168, 92, 66)))
    shape = cfg.get("greeting_bg_shape", DEFAULT_BG_SHAPE)

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = probe.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad_x, pad_y = 120, 85
    w, h = int(text_w + pad_x * 2), int(text_h + pad_y * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = w / 2, h / 2

    # Every pixel drawn here is fully opaque or fully transparent, never
    # in between: Tk's -transparentcolor is a hard binary color-key, not
    # real alpha blending, so a true Gaussian-blurred glow would leave a
    # visible fringe where partially transparent pixels get flattened
    # onto the key color.
    if shape == "glow_orb":
        dim = tuple(max(0, int(c * 0.11)) for c in bg_color)
        base_rx, base_ry = text_w * 0.75, h * 0.42
        for i in range(_GLOW_BAND_COUNT, 0, -1):
            t = i / _GLOW_BAND_COUNT
            mult = 0.55 + 0.95 * t
            blend = 1.0 - t
            color = tuple(int(dim[c] + (bg_color[c] - dim[c]) * blend) for c in range(3))
            rx, ry = base_rx * mult, base_ry * mult
            draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(*color, 255))
    elif shape == "rounded_rect":
        pad = 30
        draw.rounded_rectangle([pad, pad, w - pad, h - pad], radius=28, fill=(*bg_color, 255))
    elif shape == "rectangle":
        pad = 30
        draw.rectangle([pad, pad, w - pad, h - pad], fill=(*bg_color, 255))
    # "none": no panel at all -- text floats directly on the transparent key.

    tx = (w - text_w) / 2 - bbox[0]
    ty = (h - text_h) / 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=(*text_color, 255))

    return img


def _render_image_greeting(image_path):
    try:
        src = Image.open(image_path).convert("RGBA")
    except (OSError, ValueError):
        return None
    w, h = src.size
    scale = min(1.0, _MAX_IMAGE_DIM / max(w, h))
    if scale < 1.0:
        src = src.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return src


def render_greeting_preview(cfg, name="Naga"):
    """Build the composed image a real greeting would show, for a live
    preview in the settings dialog. Same selection logic as
    JarvisGreeting.__init__."""
    if cfg.get("greeting_use_custom_image") and cfg.get("greeting_image_path"):
        img = _render_image_greeting(cfg["greeting_image_path"])
        if img is not None:
            return img
    return _render_text_greeting(cfg, name)


class JarvisGreeting:
    def __init__(self, root, center_x, center_y, name, cfg):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG_COLOR)
        try:
            self.win.attributes("-transparentcolor", BG_COLOR)
        except tk.TclError:
            pass

        pil_img = render_greeting_preview(cfg, name)
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
