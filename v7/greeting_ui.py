"""Settings dialog for customizing the Jarvis-style greeting: text, font,
colors, background shape, or swapping in a user-supplied image instead
of text entirely. Edits a copy of the config dict and only writes it
back into the caller's cfg on Save."""

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from greeting import (
    BG_SHAPE_CHOICES,
    DEFAULT_BG_COLOR,
    DEFAULT_BG_SHAPE,
    DEFAULT_FONT,
    DEFAULT_TEXT,
    DEFAULT_TEXT_COLOR,
    FONT_CHOICES,
    JarvisGreeting,
    save_greeting_image,
)

_SHAPE_LABELS = list(BG_SHAPE_CHOICES.keys())
_SHAPE_VALUES = {v: k for k, v in BG_SHAPE_CHOICES.items()}


class GreetingSettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_saved):
        super().__init__(parent)
        self.title("Customize Greeting")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.cfg = cfg
        self.on_saved = on_saved
        self._image_path = cfg.get("greeting_image_path", "")
        self._preview_win = None

        pad = ttk.Frame(self, padding=16)
        pad.grid(row=0, column=0)
        row = 0

        ttk.Label(pad, text="Greeting text (use {name} for the recognized name):").grid(
            row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.text_var = tk.StringVar(value=cfg.get("greeting_text", DEFAULT_TEXT))
        ttk.Entry(pad, textvariable=self.text_var, width=42).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(0, 10)); row += 1

        ttk.Label(pad, text="Font:").grid(row=row, column=0, sticky="w")
        self.font_var = tk.StringVar(value=cfg.get("greeting_font", DEFAULT_FONT))
        font_combo = ttk.Combobox(pad, textvariable=self.font_var, values=list(FONT_CHOICES.keys()),
                                   state="readonly", width=30)
        font_combo.grid(row=row, column=1, sticky="ew", pady=(0, 6)); row += 1

        ttk.Label(pad, text="Text color:").grid(row=row, column=0, sticky="w")
        self.text_color = cfg.get("greeting_text_color", DEFAULT_TEXT_COLOR)
        self.text_color_swatch = tk.Label(pad, width=6, bg=self.text_color, relief="solid", borderwidth=1)
        self.text_color_swatch.grid(row=row, column=1, sticky="w")
        ttk.Button(pad, text="Change...", command=self._pick_text_color).grid(
            row=row, column=1, sticky="e", pady=(0, 6)); row += 1

        ttk.Label(pad, text="Background shape:").grid(row=row, column=0, sticky="w")
        self.shape_var = tk.StringVar(
            value=_SHAPE_VALUES.get(cfg.get("greeting_bg_shape", DEFAULT_BG_SHAPE), _SHAPE_LABELS[0]))
        shape_combo = ttk.Combobox(pad, textvariable=self.shape_var, values=_SHAPE_LABELS,
                                    state="readonly", width=30)
        shape_combo.grid(row=row, column=1, sticky="ew", pady=(0, 6)); row += 1

        ttk.Label(pad, text="Background color:").grid(row=row, column=0, sticky="w")
        self.bg_color = cfg.get("greeting_bg_color", DEFAULT_BG_COLOR)
        self.bg_color_swatch = tk.Label(pad, width=6, bg=self.bg_color, relief="solid", borderwidth=1)
        self.bg_color_swatch.grid(row=row, column=1, sticky="w")
        ttk.Button(pad, text="Change...", command=self._pick_bg_color).grid(
            row=row, column=1, sticky="e", pady=(0, 6)); row += 1

        ttk.Separator(pad, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=8); row += 1

        self.use_image_var = tk.BooleanVar(value=cfg.get("greeting_use_custom_image", False))
        ttk.Checkbutton(pad, text="Show an image instead of text", variable=self.use_image_var).grid(
            row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.image_name_var = tk.StringVar(value=self._image_display_name())
        ttk.Label(pad, textvariable=self.image_name_var, foreground="#666").grid(
            row=row, column=0, sticky="w")
        ttk.Button(pad, text="Choose Image...", command=self._choose_image).grid(
            row=row, column=1, sticky="e", pady=(0, 6)); row += 1

        ttk.Separator(pad, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=8); row += 1

        btn_row = ttk.Frame(pad)
        btn_row.grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Button(btn_row, text="Preview", command=self._preview).pack(side="left")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btn_row, text="Save", command=self._save).pack(side="right", padx=(0, 6))

    def _image_display_name(self):
        import os
        return os.path.basename(self._image_path) if self._image_path else "(no image chosen)"

    def _pick_text_color(self):
        rgb, hex_color = colorchooser.askcolor(color=self.text_color, parent=self, title="Text color")
        if hex_color:
            self.text_color = hex_color
            self.text_color_swatch.configure(bg=hex_color)

    def _pick_bg_color(self):
        rgb, hex_color = colorchooser.askcolor(color=self.bg_color, parent=self, title="Background color")
        if hex_color:
            self.bg_color = hex_color
            self.bg_color_swatch.configure(bg=hex_color)

    def _choose_image(self):
        path = filedialog.askopenfilename(
            parent=self, title="Choose greeting image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            dest = save_greeting_image(path)
        except OSError as e:
            messagebox.showerror("Couldn't load image", str(e), parent=self)
            return
        self._image_path = dest
        self.image_name_var.set(self._image_display_name())
        self.use_image_var.set(True)

    def _draft_cfg(self):
        draft = dict(self.cfg)
        draft["greeting_text"] = self.text_var.get() or DEFAULT_TEXT
        draft["greeting_font"] = self.font_var.get()
        draft["greeting_text_color"] = self.text_color
        draft["greeting_bg_shape"] = BG_SHAPE_CHOICES.get(self.shape_var.get(), DEFAULT_BG_SHAPE)
        draft["greeting_bg_color"] = self.bg_color
        draft["greeting_use_custom_image"] = self.use_image_var.get()
        draft["greeting_image_path"] = self._image_path
        return draft

    def _preview(self):
        if self.use_image_var.get() and not self._image_path:
            messagebox.showinfo("No image chosen", "Choose an image first, or uncheck "
                                                     "\"Show an image instead of text\".", parent=self)
            return
        w = self.winfo_screenwidth()
        h = self.winfo_screenheight()
        JarvisGreeting(self, w / 2, h / 2, "Naga", self._draft_cfg())

    def _save(self):
        if self.use_image_var.get() and not self._image_path:
            messagebox.showinfo("No image chosen", "Choose an image first, or uncheck "
                                                     "\"Show an image instead of text\".", parent=self)
            return
        self.cfg.update(self._draft_cfg())
        if self.on_saved:
            self.on_saved()
        self.destroy()
