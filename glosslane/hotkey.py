"""A system-wide hotkey (RegisterHotKey), delivered as a Qt signal."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
HOTKEY_ID = 0x6C61

user32 = ctypes.WinDLL("user32")
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

_SPECIAL = {
    Qt.Key.Key_Space: 0x20, Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Home: 0x24,
    Qt.Key.Key_End: 0x23, Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Pause: 0x13,
    Qt.Key.Key_ScrollLock: 0x91, Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27,
    Qt.Key.Key_Down: 0x28,
}


def parse(text: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+S' -> (modifiers, virtual key)."""
    seq = QKeySequence(text)
    if seq.isEmpty():
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    m = MOD_NOREPEAT
    if mods & Qt.KeyboardModifier.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.KeyboardModifier.AltModifier:
        m |= MOD_ALT
    if mods & Qt.KeyboardModifier.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.KeyboardModifier.MetaModifier:
        m |= MOD_WIN
    k = int(key.value) if hasattr(key, "value") else int(key)
    if ord("A") <= k <= ord("Z") or ord("0") <= k <= ord("9"):
        vk = k
    elif int(Qt.Key.Key_F1.value) <= k <= int(Qt.Key.Key_F24.value):
        vk = 0x70 + k - int(Qt.Key.Key_F1.value)
    else:
        vk = _SPECIAL.get(key)
    return (m, vk) if vk else None


class Hotkey(QObject, QAbstractNativeEventFilter):
    pressed = Signal()

    def __init__(self, app):
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self.registered = False
        app.installNativeEventFilter(self)

    def set(self, text: str) -> bool:
        if self.registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False
        parsed = parse(text) if text else None
        if not parsed:
            return not text
        self.registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, parsed[0], parsed[1]))
        return self.registered

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.pressed.emit()
                return True, 0
        return False, 0
