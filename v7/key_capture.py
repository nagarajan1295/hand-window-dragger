"""Tkinter hotkey-combo capture for recording a custom gesture's keyboard
action.

Tk's per-event modifier `state` bitmask is unreliable for Alt on Windows,
so instead of reading it we track modifier keydown/keyup on the widget
ourselves and combine whatever's currently held with the next
non-modifier key -- reliable, and needs no global hook since the
recording dialog already has keyboard focus.
"""

import win32api

MODIFIER_KEYSYMS = {
    "Control_L": 0x11, "Control_R": 0x11,
    "Shift_L": 0x10, "Shift_R": 0x10,
    "Alt_L": 0x12, "Alt_R": 0x12,
}

_DISPLAY_NAMES = {0x11: "Ctrl", 0x10: "Shift", 0x12: "Alt"}

_SPECIAL_VK = {
    "Return": 0x0D, "Escape": 0x1B, "Tab": 0x09, "space": 0x20,
    "BackSpace": 0x08, "Delete": 0x2E, "Up": 0x26, "Down": 0x28,
    "Left": 0x25, "Right": 0x27, "Home": 0x24, "End": 0x23,
    "Prior": 0x21, "Next": 0x22, "Insert": 0x2D,
}
for _i in range(1, 13):
    _SPECIAL_VK[f"F{_i}"] = 0x70 + _i - 1


def keysym_to_vk(keysym):
    """Return (vk_code, display_name) for a Tk keysym, or None if it can't
    be mapped to a Windows virtual-key code."""
    if keysym in _SPECIAL_VK:
        return _SPECIAL_VK[keysym], keysym
    if len(keysym) == 1:
        packed = win32api.VkKeyScan(keysym)
        vk = packed & 0xFF
        if vk != 0xFF:
            return vk, keysym.upper()
    return None


class ComboRecorder:
    """Bind to a widget; on_captured(vk_codes, display_str) fires once a
    non-modifier key is pressed, combined with whatever modifiers are
    currently held."""

    def __init__(self, on_captured):
        self.on_captured = on_captured
        self._held_mods = []  # [(vk, display_name), ...] in press order

    def bind(self, widget):
        widget.bind("<KeyPress>", self._on_press)
        widget.bind("<KeyRelease>", self._on_release)

    def reset(self):
        self._held_mods = []

    def _on_press(self, event):
        keysym = event.keysym
        if keysym in MODIFIER_KEYSYMS:
            vk = MODIFIER_KEYSYMS[keysym]
            if vk not in [v for v, _ in self._held_mods]:
                self._held_mods.append((vk, _DISPLAY_NAMES[vk]))
            return "break"

        mapped = keysym_to_vk(keysym)
        if mapped is None:
            return "break"
        vk, name = mapped
        combo_vks = [v for v, _ in self._held_mods] + [vk]
        combo_names = [n for _, n in self._held_mods] + [name]
        self.on_captured(combo_vks, "+".join(combo_names))
        return "break"

    def _on_release(self, event):
        keysym = event.keysym
        if keysym in MODIFIER_KEYSYMS:
            vk = MODIFIER_KEYSYMS[keysym]
            self._held_mods = [(v, n) for v, n in self._held_mods if v != vk]
        return "break"
