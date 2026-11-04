"""The subtitle bar: always on top, never takes a click or the focus."""

from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from .config import Settings
from .languages import RTL

HWND_TOPMOST = -1
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
FLAGS = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus
         | Qt.WindowType.NoDropShadowWindowHint)


class Overlay(QWidget):
    def __init__(self, settings: Settings):
        super().__init__(None, FLAGS)
        self.s = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Glosslane subtitles")
        self.lines: list[dict] = []     # finished sentences, newest last
        self.current: dict | None = None  # the sentence being spoken
        self.last_change = 0.0
        self.opacity = 0.0
        self.shown_at = 0.0             # when the newest text was painted, for latency tests
        self.preview = False
        self._fade = QTimer(self, interval=50, timeout=self._tick)
        self._fade.start()
        self._top = QTimer(self, interval=2000, timeout=self._stay_on_top)
        self._top.start()
        self.place()

    # -- geometry --

    def screen_rect(self) -> QRect:
        screens = QGuiApplication.screens()
        screen = screens[self.s.monitor] if 0 <= self.s.monitor < len(screens) else QGuiApplication.primaryScreen()
        return screen.geometry()

    def place(self) -> None:
        g = self.screen_rect()
        w = int(g.width() * self.s.width / 100)
        h = int(g.height() * 0.42)
        x = g.x() + (g.width() - w) // 2
        y = g.y() + int(g.height() * self.s.offset / 100)
        if self.s.position == "bottom":
            y = g.y() + g.height() - h - int(g.height() * self.s.offset / 100)
        self.setGeometry(x, y, w, h)
        self.update()

    def _stay_on_top(self) -> None:
        # Borderless games and video players can end up above us; quietly go back on top.
        if self.isVisible() and self.opacity > 0:
            ctypes.windll.user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)

    # -- content --

    def show_partial(self, text: str, translation: str, language: str) -> None:
        self.current = {"text": text, "tr": translation, "lang": language}
        self._changed()

    def show_final(self, text: str, translation: str, language: str) -> None:
        self.current = None
        if text:
            self.lines.append({"text": text, "tr": translation, "lang": language})
            self.lines = self.lines[-2:]
        self._changed()

    def clear(self) -> None:
        self.lines, self.current = [], None
        self.opacity = 0.0
        self.update()

    def set_preview(self, on: bool) -> None:
        self.preview = on
        if on:
            self.lines = [{"text": "Thank you for subscribing to the channel.",
                           "tr": "شكرًا على الاشتراك في القناة.", "lang": "en"}]
            if self.s.target_language not in ("ar",):
                self.lines[0]["tr"] = "Glosslane · " + self.s.target_language
            self.current = None
            self._changed()
            self.show()
        else:
            self.clear()

    def _changed(self) -> None:
        self.last_change = time.monotonic()
        self.opacity = 1.0
        if self.s.enabled and not self.isVisible():
            self.show()
        self.update()

    def _tick(self) -> None:
        if self.preview or self.opacity <= 0:
            return
        idle = time.monotonic() - self.last_change
        if idle > self.s.hide_after:
            self.opacity = max(0.0, self.opacity - 0.08)
            if self.opacity == 0:
                self.lines, self.current = [], None
            self.update()

    # -- painting --

    def _blocks(self) -> list[tuple[str, bool, bool]]:
        """(text, is_original, is_old) from top to bottom."""
        items = list(self.lines)
        if self.current:
            items.append(self.current)
        out = []
        for i, item in enumerate(items[-2:]):
            old = i == 0 and len(items[-2:]) == 2
            main = item["tr"] or item["text"]
            out.append((main, False, old))
            if self.s.show_original and item["tr"] and item["text"] != item["tr"]:
                out.append((item["text"], True, old))
        return out

    def paintEvent(self, _event) -> None:  # noqa: N802
        blocks = self._blocks()
        if not blocks or self.opacity <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setOpacity(self.opacity)
        pad = int(self.s.font_size * 0.5)
        inner = self.width() - 2 * pad
        flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap)
        laid = []
        for text, original, old in blocks:
            font = QFont(self.s.font_family)
            font.setPixelSize(int(self.s.font_size * (0.62 if original else 1.0)))
            font.setWeight(QFont.Weight.Normal if original else QFont.Weight.DemiBold)
            r = QFontMetrics(font).boundingRect(QRect(0, 0, inner, 10000), flags, text)
            laid.append((text, original, old, font, r))
        gap = int(self.s.font_size * 0.18)
        total_h = sum(r.height() for *_, r in laid) + gap * (len(laid) - 1) + 2 * pad
        widest = max(r.width() for *_, r in laid) + 2 * pad
        total_h = min(total_h, self.height())
        x = (self.width() - widest) / 2
        y = self.height() - total_h if self.s.position == "bottom" else 0
        box = QRectF(x, y, widest, total_h)
        if self.s.background > 0:
            path = QPainterPath()
            path.addRoundedRect(box, self.s.font_size * 0.35, self.s.font_size * 0.35)
            p.fillPath(path, QColor(8, 10, 14, int(255 * self.s.background / 100)))
        cy = y + pad
        rtl_main = self.s.target_language in RTL
        for text, original, old, font, r in laid:
            p.setFont(font)
            p.setLayoutDirection(Qt.LayoutDirection.RightToLeft if (rtl_main and not original)
                                 else Qt.LayoutDirection.LeftToRight)
            rect = QRect(pad, int(cy), inner, r.height())
            shadow = QColor(0, 0, 0, 200)
            for dx, dy in ((0, 2), (1, 1), (-1, 1)):
                p.setPen(shadow)
                p.drawText(rect.translated(dx, dy), flags, text)
            color = QColor(200, 214, 230) if original else QColor(255, 255, 255)
            if old:
                color.setAlpha(150)
            p.setPen(color)
            p.drawText(rect, flags, text)
            cy += r.height() + gap
        p.end()
        self.shown_at = time.monotonic()
