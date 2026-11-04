"""Glosslane: live translated subtitles for any sound on the PC."""

from __future__ import annotations

import ctypes
import sys
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__
from .config import DATA_DIR, Settings
from .engine import Engine, Event
from .hotkey import Hotkey
from .overlay import Overlay
from .ui import SettingsWindow, app_icon

RELOAD = {"asr_model", "translator", "online_url", "online_key", "device", "low_impact"}
LOOK = {"monitor", "position", "offset", "width", "font_size", "background", "show_original", "target_language"}
INSTANCE = "Glosslane-Niro54321"


class Bridge(QObject):
    """Engine events arrive on the engine thread; this hands them to the UI thread."""
    event = Signal(object)


class App:
    def __init__(self, qapp: QApplication, background: bool):
        self.qapp = qapp
        self.s = Settings.load()
        self.overlay = Overlay(self.s)
        self.window = SettingsWindow(self.s)
        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self.engine: Engine | None = None
        self.latency: list[float] = []

        self.hotkey = Hotkey(qapp)
        self.hotkey.pressed.connect(self.toggle)
        self.window.set_hotkey_ok(self.hotkey.set(self.s.hotkey))

        self.window.changed.connect(self._changed)
        self.window.toggled.connect(self._set_enabled)
        self.window.preview.connect(self.overlay.set_preview)
        self.window.gpu_ready.connect(self.restart)
        self.window.level_source = lambda: self.engine.level if self.engine else 0.0

        self._reload = QTimer(singleShot=True, interval=700, timeout=self.restart)
        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("Glosslane")
        menu = QMenu()
        self.act_on = QAction("Subtitles on", checkable=True, checked=self.s.enabled)
        self.act_on.triggered.connect(lambda on: self._set_enabled(on))
        menu.addAction(self.act_on)
        menu.addAction("Settings…", self.show_window)
        menu.addSeparator()
        menu.addAction("Quit Glosslane", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_window() if r == QSystemTrayIcon.ActivationReason.Trigger
                                    else None)
        self.tray.show()
        self.menu = menu

        self.restart()
        if not background or not self.s.first_run_done:
            self.show_window()
            self.s.first_run_done = True
            self.s.save()

    # -- engine --

    def restart(self) -> None:
        if self.engine:
            old, self.engine = self.engine, None
            old.on_event = lambda ev: None
            old.stop()
        self.engine = Engine(self.s, self.bridge.event.emit)
        self.engine.paused = not self.s.enabled
        self.engine.start()

    def _log(self, text: str) -> None:
        """Status and errors only (never what was said), for troubleshooting."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            log = DATA_DIR / "glosslane.log"
            if log.is_file() and log.stat().st_size > 200_000:
                log.unlink()
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def _on_event(self, ev: Event) -> None:
        if ev.kind in ("status", "error", "ready") and "Downloading" not in ev.text:
            self._log(f"{ev.kind}: {ev.text} {ev.extra or ''}".strip())
        if ev.kind == "status":
            self.window.set_status(ev.text, ev.extra.get("done", -1), ev.extra.get("total", 0))
        elif ev.kind == "error":
            self.window.set_status(ev.text)
            self.tray.showMessage("Glosslane", ev.text, QSystemTrayIcon.MessageIcon.Warning, 5000)
        elif ev.kind == "ready":
            info = ev.extra
            self.window.set_status(f"Listening · {info['asr']} on {info['device'].upper()} · "
                                   f"translator: {info['translator']}")
        elif ev.kind == "partial" and self.s.enabled:
            self.overlay.show_partial(ev.text, ev.translation, ev.language)
        elif ev.kind == "final" and self.s.enabled:
            self.overlay.show_final(ev.text, ev.translation, ev.language)
            if ev.text and ev.speech_end:
                self.latency = (self.latency + [time.monotonic() - ev.speech_end])[-20:]
                avg = sum(self.latency) / len(self.latency)
                info = self.engine.info if self.engine else {}
                self.window.set_status(f"Listening · {info.get('asr', '')} on {info.get('device', '').upper()} · "
                                       f"delay after a sentence ends ≈ {avg:.1f} s")

    # -- settings --

    def _changed(self, name: str) -> None:
        if name in RELOAD:
            self._reload.start()
        elif name == "source" and self.engine:
            self.engine.change_source()
        elif name == "hotkey":
            self.window.set_hotkey_ok(self.hotkey.set(self.s.hotkey))
        if name in LOOK:
            self.overlay.place()
            if self.overlay.preview:
                self.overlay.set_preview(True)

    def _set_enabled(self, on: bool) -> None:
        self.s.enabled = on
        self.s.save()
        self.act_on.setChecked(on)
        self.window.set_enabled(on)
        if self.engine:
            self.engine.paused = not on
        if on:
            self.overlay.show()
            self.tray.showMessage("Glosslane", "Subtitles on", QSystemTrayIcon.MessageIcon.NoIcon, 1200)
        else:
            self.overlay.clear()
            self.overlay.hide()
            self.tray.showMessage("Glosslane", "Subtitles off", QSystemTrayIcon.MessageIcon.NoIcon, 1200)

    def toggle(self) -> None:
        self._set_enabled(not self.s.enabled)

    def show_window(self) -> None:
        self.window.refresh_sources()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        self.hotkey.set("")
        self.tray.hide()
        if self.engine:
            self.engine.on_event = lambda ev: None
            self.engine.stop()
        self.qapp.quit()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Niro54321.Glosslane")
    except (AttributeError, OSError):
        pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(argv)
    qapp.setApplicationName("Glosslane")
    qapp.setApplicationVersion(__version__)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(app_icon())

    # One copy at a time: a second launch just opens the first one's settings.
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(INSTANCE)
    server = QLocalServer()
    server.listen(INSTANCE)

    app = App(qapp, background="--background" in argv)

    def incoming():
        sock = server.nextPendingConnection()
        if sock:
            sock.readyRead.connect(app.show_window)
    server.newConnection.connect(incoming)
    return qapp.exec()
