"""Settings, kept as JSON in %APPDATA%\\Glosslane\\settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP = "Glosslane"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP
MODELS_DIR = DATA_DIR / "models"
GPU_DIR = DATA_DIR / "gpu"


@dataclass
class Settings:
    # what to listen to
    source_mode: str = "device"        # "device" (everything on a playback device) or "app"
    device_id: str = ""                # "" = Windows' default playback device
    app_exe: str = ""                  # e.g. "Discord.exe" when source_mode == "app"
    # languages
    source_language: str = "auto"
    target_language: str = "ar"
    # engines
    asr_model: str = "auto"            # auto (turbo on GPU, base on CPU), tiny … large-v3
    translator: str = "m2m100_418M"    # m2m100_418M, madlad400_3b, online, none
    online_url: str = ""               # a LibreTranslate-compatible server, only used by "online"
    online_key: str = ""
    device: str = "auto"               # auto, cuda, cpu
    low_impact: bool = True            # below-normal priority, few CPU threads
    # subtitles
    enabled: bool = True
    show_original: bool = False
    font_size: int = 30
    font_family: str = "Segoe UI"
    position: str = "bottom"           # bottom or top
    offset: int = 8                    # % of the screen height from that edge
    monitor: int = 0                   # index in QGuiApplication.screens()
    width: int = 70                    # % of the screen width
    background: int = 55               # % opacity of the box behind the text
    hide_after: float = 6.0            # seconds of quiet before the subtitle fades
    hotkey: str = "Ctrl+Alt+S"
    # timing
    silence_ms: int = 450              # pause that ends a sentence
    max_sentence: float = 7.0          # seconds before a long run of speech is cut
    partials: bool = True              # show words while the sentence is still being spoken
    first_run_done: bool = False

    @classmethod
    def load(cls) -> "Settings":
        path = CONFIG_DIR / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_DIR / "settings.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_DIR / "settings.json")
