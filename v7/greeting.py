"""JARVIS-style greeting overlay: big HUD text that holds briefly, then
fades out. Shown when the display wakes because a recognized face
returned (see engine.py's presence-display feature). Main-thread only.
"""

import tkinter as tk

HOLD_MS = 900       # how long it stays fully visible before fading
FADE_MS = 1300       # fade duration -- moderate, not instant, not sluggish
FADE_STEPS = 24
BG_COLOR = "#010102"
TEXT_COLOR = "#00e5ff"


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

        text = f"HI {name.upper()}"
        label = tk.Label(self.win, text=text, font=("Consolas", 46, "bold"),
                          fg=TEXT_COLOR, bg=BG_COLOR)
        label.pack(padx=40, pady=24)

        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        h = self.win.winfo_reqheight()
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
