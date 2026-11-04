"""The settings window: Windows 11 acrylic glass behind translucent cards."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QKeySequenceEdit, QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QSlider, QVBoxLayout, QWidget)

from . import audio, models
from .config import Settings
from .languages import LANGUAGES, WHISPER_UNKNOWN, label

ACCENT = "#3DD6C6"

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI'; font-size: 13px; color: #E9EEF5; }}
QWidget#root {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QFrame#card {{ background: rgba(255,255,255,0.055); border: 1px solid rgba(255,255,255,0.09);
               border-radius: 14px; }}
QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#h {{ font-size: 12px; font-weight: 600; color: rgba(233,238,245,0.62); }}
QLabel#muted {{ color: rgba(233,238,245,0.6); }}
QLabel#status {{ color: rgba(233,238,245,0.75); }}
QComboBox, QLineEdit, QKeySequenceEdit {{ background: rgba(0,0,0,0.28); border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px; padding: 5px 9px; min-height: 20px; }}
QComboBox:hover, QLineEdit:hover {{ border-color: rgba(255,255,255,0.25); }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1B2027; border: 1px solid rgba(255,255,255,0.12);
    selection-background-color: rgba(61,214,198,0.28); outline: none; padding: 4px; }}
QPushButton {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px;
    padding: 6px 14px; }}
QPushButton:hover {{ background: rgba(255,255,255,0.14); }}
QPushButton#primary {{ background: {ACCENT}; color: #06201D; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #5FE3D5; }}
QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,0.14); border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}
QCheckBox::indicator {{ border-radius: 5px; border: 1px solid rgba(255,255,255,0.3); background: rgba(0,0,0,0.25); }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator {{ border-radius: 9px; border: 1px solid rgba(255,255,255,0.3); background: rgba(0,0,0,0.25); }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
    stop:0 #06201D, stop:0.35 #06201D, stop:0.45 {ACCENT}, stop:1 {ACCENT}); border-color: {ACCENT}; }}
QProgressBar {{ background: rgba(255,255,255,0.1); border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def app_icon() -> QIcon:
    """Two subtitle bars in a rounded square: drawn, so no image files to ship."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#14323A"))
        p.drawRoundedRect(0, 0, size, size, size * 0.24, size * 0.24)
        p.setBrush(QColor(ACCENT))
        h = max(2.0, size * 0.12)
        p.drawRoundedRect(size * 0.18, size * 0.52, size * 0.64, h, h / 2, h / 2)
        p.setBrush(QColor(255, 255, 255, 220))
        p.drawRoundedRect(size * 0.28, size * 0.70, size * 0.44, h, h / 2, h / 2)
        p.setBrush(QColor(255, 255, 255, 90))
        p.drawEllipse(size * 0.2, size * 0.2, size * 0.18, size * 0.18)
        p.drawEllipse(size * 0.44, size * 0.2, size * 0.18, size * 0.18)
        p.end()
        icon.addPixmap(pm)
    return icon


def _glass(widget: QWidget) -> None:
    """Dark title bar and acrylic backdrop (Windows 11); a plain dark window elsewhere."""
    hwnd = wintypes.HWND(int(widget.winId()))
    dwm = ctypes.windll.dwmapi

    def attr(key: int, value: int) -> int:
        v = ctypes.c_int(value)
        return dwm.DwmSetWindowAttribute(hwnd, key, ctypes.byref(v), ctypes.sizeof(v))

    attr(20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
    ok = attr(38, 3) == 0  # DWMWA_SYSTEMBACKDROP_TYPE = DWMSBT_TRANSIENTWINDOW (acrylic)

    class MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
    if ok:
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))
    widget.setProperty("acrylic", ok)


def card(title: str) -> tuple[QFrame, QGridLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    grid = QGridLayout(frame)
    grid.setContentsMargins(16, 14, 16, 16)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(10)
    h = QLabel(title.upper())
    h.setObjectName("h")
    grid.addWidget(h, 0, 0, 1, 3)
    return frame, grid


def slider(lo: int, hi: int, value: int) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    return s


class Level(QWidget):
    """A small live meter of what the source is playing."""

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedSize(90, 6)

    def set(self, rms: float) -> None:
        v = min(1.0, (rms * 6) ** 0.6)
        self.value = max(v, self.value * 0.8)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(self.rect(), 3, 3)
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(0, 0, int(self.width() * self.value), self.height(), 3, 3)
        p.end()


class SettingsWindow(QWidget):
    changed = Signal(str)         # name of the setting that changed
    toggled = Signal(bool)        # subtitles on/off
    gpu_ready = Signal()
    preview = Signal(bool)
    _gpu_progress = Signal(int, int)
    _gpu_done = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.setWindowTitle("Glosslane")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(QSS)
        self.resize(560, 820)
        self._building = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(18, 16, 18, 18)
        col.setSpacing(12)

        # header
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(40, 40)))
        head.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        t = QLabel("Glosslane")
        t.setObjectName("title")
        names.addWidget(t)
        self.status = QLabel("Starting…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        names.addWidget(self.status)
        head.addLayout(names, 1)
        right = QVBoxLayout()
        self.on = QCheckBox("Subtitles on")
        self.on.setChecked(settings.enabled)
        self.on.toggled.connect(self._toggle)
        right.addWidget(self.on)
        self.level = Level()
        right.addWidget(self.level, 0, Qt.AlignmentFlag.AlignRight)
        head.addLayout(right)
        col.addLayout(head)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        col.addWidget(self.progress)

        # GPU pack
        self.gpu_card, g = card("NVIDIA GPU found")
        self.gpu_text = QLabel("Download the GPU pack (NVIDIA CUDA libraries, about 1.2 GB, once) for the large "
                               "speech model: faster and much more accurate subtitles.")
        self.gpu_text.setWordWrap(True)
        self.gpu_text.setObjectName("muted")
        g.addWidget(self.gpu_text, 1, 0, 1, 2)
        self.gpu_btn = QPushButton("Download")
        self.gpu_btn.setObjectName("primary")
        self.gpu_btn.clicked.connect(self._download_gpu)
        g.addWidget(self.gpu_btn, 1, 2)
        self.gpu_card.setVisible(models.has_nvidia_gpu() and not models.gpu_pack_ready())
        col.addWidget(self.gpu_card)
        self._gpu_progress.connect(self._on_gpu_progress)
        self._gpu_done.connect(self._on_gpu_done)

        # source
        c, g = card("Listen to")
        self.src_all = QRadioButton("Everything playing on")
        self.src_app = QRadioButton("Only this app")
        grp = QButtonGroup(self)
        grp.addButton(self.src_all)
        grp.addButton(self.src_app)
        (self.src_app if settings.source_mode == "app" else self.src_all).setChecked(True)
        self.devices = QComboBox()
        self.apps = QComboBox()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_sources)
        g.addWidget(self.src_all, 1, 0)
        g.addWidget(self.devices, 1, 1, 1, 2)
        g.addWidget(self.src_app, 2, 0)
        g.addWidget(self.apps, 2, 1)
        g.addWidget(refresh, 2, 2)
        hint = QLabel("Discord, a browser, a game… Apps show here while they have sound open.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        g.addWidget(hint, 3, 0, 1, 3)
        g.setColumnStretch(1, 1)
        col.addWidget(c)
        self.src_all.toggled.connect(self._source_changed)
        self.devices.currentIndexChanged.connect(self._source_changed)
        self.apps.currentIndexChanged.connect(self._source_changed)

        # languages
        c, g = card("Languages")
        self.spoken = QComboBox()
        self.spoken.addItem("Detect automatically", "auto")
        self.target = QComboBox()
        for code in sorted(LANGUAGES, key=lambda k: LANGUAGES[k][0]):
            if code not in WHISPER_UNKNOWN:
                self.spoken.addItem(label(code), code)
            self.target.addItem(label(code), code)
        self._select(self.spoken, settings.source_language)
        self._select(self.target, settings.target_language)
        g.addWidget(QLabel("Spoken language"), 1, 0)
        g.addWidget(self.spoken, 1, 1)
        g.addWidget(QLabel("Subtitles in"), 2, 0)
        g.addWidget(self.target, 2, 1)
        self.original = QCheckBox("Also show the original words")
        self.original.setChecked(settings.show_original)
        g.addWidget(self.original, 3, 0, 1, 2)
        g.setColumnStretch(1, 1)
        col.addWidget(c)
        self.spoken.currentIndexChanged.connect(lambda: self._set("source_language", self.spoken.currentData()))
        self.target.currentIndexChanged.connect(lambda: self._set("target_language", self.target.currentData()))
        self.original.toggled.connect(lambda v: self._set("show_original", v))

        # look
        c, g = card("Subtitles")
        self.screens = QComboBox()
        for i, sc in enumerate(QGuiApplication.screens()):
            geo = sc.geometry()
            self.screens.addItem(f"Screen {i + 1}: {sc.name().strip(chr(92) + '.')} ({geo.width()}×{geo.height()})", i)
        self.screens.setCurrentIndex(min(settings.monitor, self.screens.count() - 1))
        self.where = QComboBox()
        self.where.addItem("Bottom", "bottom")
        self.where.addItem("Top", "top")
        self._select(self.where, settings.position)
        rows = [("Screen", self.screens), ("Position", self.where)]
        self.size = slider(14, 72, settings.font_size)
        self.offset = slider(0, 40, settings.offset)
        self.width_s = slider(30, 100, settings.width)
        self.bg = slider(0, 90, settings.background)
        rows += [("Text size", self.size), ("Distance from edge", self.offset), ("Width", self.width_s),
                 ("Background", self.bg)]
        for i, (name, w) in enumerate(rows, start=1):
            g.addWidget(QLabel(name), i, 0)
            g.addWidget(w, i, 1, 1, 2)
        self.preview_btn = QPushButton("Show a sample")
        self.preview_btn.setCheckable(True)
        self.preview_btn.toggled.connect(self.preview.emit)
        g.addWidget(self.preview_btn, len(rows) + 1, 2)
        g.setColumnStretch(1, 1)
        col.addWidget(c)
        self.screens.currentIndexChanged.connect(lambda: self._set("monitor", self.screens.currentData()))
        self.where.currentIndexChanged.connect(lambda: self._set("position", self.where.currentData()))
        self.size.valueChanged.connect(lambda v: self._set("font_size", v))
        self.offset.valueChanged.connect(lambda v: self._set("offset", v))
        self.width_s.valueChanged.connect(lambda v: self._set("width", v))
        self.bg.valueChanged.connect(lambda v: self._set("background", v))

        # engine
        c, g = card("Speech and translation")
        self.asr = QComboBox()
        self.asr.addItem("Automatic (Large v3 Turbo on GPU, Base on CPU)", "auto")
        for key, m in models.WHISPER.items():
            self.asr.addItem(m.label, key)
        self._select(self.asr, settings.asr_model)
        self.tr = QComboBox()
        for key, m in models.TRANSLATORS.items():
            self.tr.addItem(m.label, key)
        self.tr.addItem("Online server (sends text off this PC)", "online")
        self.tr.addItem("Off (show what was said)", "none")
        self._select(self.tr, settings.translator)
        self.url = QLineEdit(settings.online_url)
        self.url.setPlaceholderText("LibreTranslate address, e.g. http://localhost:5000")
        self.key = QLineEdit(settings.online_key)
        self.key.setPlaceholderText("API key (if the server needs one)")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.proc = QComboBox()
        for text, val in (("Automatic", "auto"), ("GPU (NVIDIA CUDA)", "cuda"), ("CPU", "cpu")):
            self.proc.addItem(text, val)
        self._select(self.proc, settings.device)
        self.partials = QCheckBox("Show words while the sentence is still being spoken")
        self.partials.setChecked(settings.partials)
        self.low = QCheckBox("Low impact: leave the PC's power to games and other apps")
        self.low.setChecked(settings.low_impact)
        self.pause = slider(250, 1200, settings.silence_ms)
        g.addWidget(QLabel("Speech model"), 1, 0)
        g.addWidget(self.asr, 1, 1)
        g.addWidget(QLabel("Translator"), 2, 0)
        g.addWidget(self.tr, 2, 1)
        g.addWidget(self.url, 3, 1)
        g.addWidget(self.key, 4, 1)
        g.addWidget(QLabel("Processor"), 5, 0)
        g.addWidget(self.proc, 5, 1)
        g.addWidget(QLabel("Sentence ends after"), 6, 0)
        g.addWidget(self.pause, 6, 1)
        g.addWidget(self.partials, 7, 0, 1, 2)
        g.addWidget(self.low, 8, 0, 1, 2)
        g.setColumnStretch(1, 1)
        col.addWidget(c)
        self._online_fields()
        self.asr.currentIndexChanged.connect(lambda: self._set("asr_model", self.asr.currentData()))
        self.tr.currentIndexChanged.connect(lambda: (self._online_fields(), self._set("translator", self.tr.currentData())))
        self.url.editingFinished.connect(lambda: self._set("online_url", self.url.text().strip()))
        self.key.editingFinished.connect(lambda: self._set("online_key", self.key.text().strip()))
        self.proc.currentIndexChanged.connect(lambda: self._set("device", self.proc.currentData()))
        self.partials.toggled.connect(lambda v: self._set("partials", v))
        self.low.toggled.connect(lambda v: self._set("low_impact", v))
        self.pause.valueChanged.connect(lambda v: self._set("silence_ms", v))

        # hotkey
        c, g = card("Shortcut")
        self.hotkey = QKeySequenceEdit(QKeySequence(settings.hotkey))
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey_note = QLabel("Turns the subtitles on and off from anywhere, even in a game.")
        self.hotkey_note.setObjectName("muted")
        self.hotkey_note.setWordWrap(True)
        g.addWidget(QLabel("Show / hide"), 1, 0)
        g.addWidget(self.hotkey, 1, 1)
        g.addWidget(self.hotkey_note, 2, 0, 1, 2)
        g.setColumnStretch(1, 1)
        col.addWidget(c)
        self.hotkey.editingFinished.connect(
            lambda: self._set("hotkey", self.hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText)))

        foot = QLabel("Everything runs on this PC: nothing you hear is uploaded. Models download once, "
                      "from Hugging Face.  ·  MIT licence  ·  github.com/Niro54321/Glosslane")
        foot.setObjectName("muted")
        foot.setWordWrap(True)
        col.addWidget(foot)
        col.addStretch(1)

        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._poll_level)
        self.level_source = None
        self.refresh_sources()
        self._building = False

    # -- helpers --

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        i = combo.findData(value)
        combo.setCurrentIndex(max(0, i))

    def _set(self, name: str, value) -> None:
        if self._building or getattr(self.s, name) == value:
            return
        setattr(self.s, name, value)
        self.s.save()
        self.changed.emit(name)

    def _toggle(self, on: bool) -> None:
        if self._building:
            return
        self.s.enabled = on
        self.s.save()
        self.toggled.emit(on)

    def set_enabled(self, on: bool) -> None:
        self.on.blockSignals(True)
        self.on.setChecked(on)
        self.on.blockSignals(False)

    def _online_fields(self) -> None:
        online = self.tr.currentData() == "online"
        self.url.setVisible(online)
        self.key.setVisible(online)

    def refresh_sources(self) -> None:
        building, self._building = self._building, True
        self.devices.clear()
        self.devices.addItem("Default playback device", "")
        try:
            for d in audio.list_devices():
                self.devices.addItem(d.name + ("  (default)" if d.default else ""), d.id)
        except OSError:
            pass
        self._select(self.devices, self.s.device_id)
        self.apps.clear()
        try:
            apps = audio.list_apps()
        except OSError:
            apps = []
        if self.s.app_exe and not any(a.exe.lower() == self.s.app_exe.lower() for a in apps):
            self.apps.addItem(f"{self.s.app_exe} (not playing now)", self.s.app_exe)
        for a in apps:
            self.apps.addItem(a.title, a.exe)
        if not self.apps.count():
            self.apps.addItem("No app is playing sound", "")
        self._select(self.apps, self.s.app_exe)
        self._building = building

    def _source_changed(self) -> None:
        if self._building:
            return
        mode = "app" if self.src_app.isChecked() else "device"
        dev, exe = self.devices.currentData() or "", self.apps.currentData() or ""
        if mode == "app" and not exe:
            return
        if (mode, dev, exe) != (self.s.source_mode, self.s.device_id, self.s.app_exe):
            self.s.source_mode, self.s.device_id, self.s.app_exe = mode, dev, exe
            self.s.save()
            self.changed.emit("source")

    def set_status(self, text: str, done: int = -1, total: int = 0) -> None:
        self.status.setText(text)
        if total > 0 and done < total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))
            self.progress.show()
        else:
            self.progress.hide()

    def set_hotkey_ok(self, ok: bool) -> None:
        self.hotkey_note.setText("Turns the subtitles on and off from anywhere, even in a game." if ok else
                                 "Another app already uses this shortcut. Pick another one.")

    def _poll_level(self) -> None:
        if self.level_source:
            self.level.set(self.level_source())

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self.property("glass_done"):
            _glass(self)
            self.setProperty("glass_done", True)
            if not self.property("acrylic"):
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
                self.setStyleSheet(QSS + "QWidget#root { background: #14181E; }")
        self._timer.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        if self.preview_btn.isChecked():
            self.preview_btn.setChecked(False)
        super().hideEvent(e)

    def closeEvent(self, e) -> None:  # noqa: N802
        e.ignore()  # the app keeps running in the tray
        self.hide()

    # -- GPU pack --

    def _download_gpu(self) -> None:
        self.gpu_btn.setEnabled(False)
        self.gpu_text.setText("Downloading the GPU pack…")

        def work():
            try:
                models.download_gpu_pack(lambda d, t: self._gpu_progress.emit(d, t))
                self._gpu_done.emit("")
            except Exception as exc:  # noqa: BLE001
                self._gpu_done.emit(str(exc))
        threading.Thread(target=work, daemon=True).start()

    def _on_gpu_progress(self, done: int, total: int) -> None:
        self.gpu_text.setText(f"Downloading the GPU pack… {done / 1e6:.0f} of {total / 1e6:.0f} MB")
        self.set_status(self.status.text(), done, total)

    def _on_gpu_done(self, error: str) -> None:
        self.progress.hide()
        if error:
            self.gpu_text.setText(f"Download failed: {error}")
            self.gpu_btn.setEnabled(True)
            return
        self.gpu_card.hide()
        self.gpu_ready.emit()
