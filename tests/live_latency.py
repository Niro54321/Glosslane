"""Live end-to-end test: play speech through the speakers, let the engine hear
it through WASAPI loopback, and measure how long the subtitles take.

    python tests/live_latency.py [--clips DIR] [--app EXE] [--translator KEY]

Clips are .wav files (any rate); English ones are made with Windows' own
text-to-speech. Prints, for each clip: what Whisper heard, the translation,
the delay from the start of speech to the first words on screen, and from
the end of speech to the finished sentence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from glosslane.config import Settings  # noqa: E402
from glosslane.engine import Engine, Event  # noqa: E402

ENGLISH = [
    "Thank you for subscribing to the channel.",
    "Today we are going to build a gaming computer from scratch, and I will show you every step.",
    "Can you hear me? I think my microphone is not working.",
    "The weather tomorrow will be sunny in the morning, with some rain in the evening.",
]


# Plays each clip on one output device, so tests can use an output nobody
# listens to (a monitor's HDMI audio) and not disturb the person at the PC.
PLAYER = """
import sys, time
from PySide6.QtCore import QCoreApplication, QUrl, QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
app = QCoreApplication([])
want = sys.argv[1].lower()
dev = next((d for d in QMediaDevices.audioOutputs() if want and want in d.description().lower()),
           QMediaDevices.defaultAudioOutput())
print(dev.id().data().decode(), flush=True)
for line in sys.stdin:
    fx = QSoundEffect()
    fx.setAudioDevice(dev)
    fx.setSource(QUrl.fromLocalFile(line.strip()))
    while fx.status() == QSoundEffect.Status.Loading:
        app.processEvents()
    fx.play()
    print(time.monotonic(), flush=True)
    loop = QEventLoop()
    fx.playingChanged.connect(lambda: fx.isPlaying() or loop.quit())
    QTimer.singleShot(30000, loop.quit)
    loop.exec()
    print("played", flush=True)
"""


def tts(text: str, path: Path) -> None:
    script = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{path}'); $s.Speak(\"{text}\"); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)


def duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", nargs="*", default=[])
    ap.add_argument("--output", default="", help="play clips on the output whose name contains this "
                    "(e.g. a monitor's HDMI audio, so nobody hears them); default: the default output")
    ap.add_argument("--per-app", action="store_true", help="capture only the player app, not the whole output")
    ap.add_argument("--translator", default="m2m100_418M")
    ap.add_argument("--asr", default="auto")
    ap.add_argument("--target", default="ar")
    ap.add_argument("--no-english", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="glosslane-live-"))
    clips = []
    if not args.no_english:
        for i, text in enumerate(ENGLISH):
            p = tmp / f"en{i}.wav"
            tts(text, p)
            clips.append(("en", p, text))
    for c in args.clips:
        clips.append((Path(c).parent.name, Path(c), ""))

    s = Settings(translator=args.translator, asr_model=args.asr, target_language=args.target)
    # The clips play in a helper process on their own output device, and the
    # engine listens to that device (or, with --per-app, to that process only).
    player = subprocess.Popen([sys.executable, "-c", PLAYER, args.output], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, bufsize=1)
    s.device_id = player.stdout.readline().strip()
    if args.per_app:
        from glosslane import audio
        audio.find_app = lambda exe: player.pid
        s.source_mode, s.app_exe = "app", "player"
    events: list[tuple[float, Event]] = []
    ready = threading.Event()

    def on_event(ev: Event) -> None:
        events.append((time.monotonic(), ev))
        if ev.kind in ("partial", "final") and ev.text and os.environ.get("GL_VERBOSE"):
            print(f"  {time.monotonic() - t0:7.2f} {ev.kind:7} [{ev.language}] {ev.text[:70]}", flush=True)
        if ev.kind in ("ready", "error"):
            print(ev.kind, ev.text, ev.extra, flush=True)
            ready.set()
        elif ev.kind == "status":
            print("  ", ev.text, flush=True)

    eng = Engine(s, on_event)
    t0 = time.monotonic()
    eng.start()
    ready.wait(1800)
    print(f"loaded in {time.monotonic() - t0:.1f} s", flush=True)
    time.sleep(1.0)

    cpu0, wall0 = time.process_time(), time.monotonic()
    plays = []
    for lang, path, text in clips:
        player.stdin.write(f"{path}\n")
        start = float(player.stdout.readline())
        player.stdout.readline()  # played
        end = start + duration(path)
        plays.append((lang, path, text, start, end))
        if os.environ.get("GL_VERBOSE"):
            print(f"  {start - t0:7.2f}-{end - t0:.2f} played {lang}/{path.name} "
                  f"capture error={eng.capture.error!r} queued={eng.capture.queue.qsize()}", flush=True)
        time.sleep(2.0)
    time.sleep(2.0)
    if os.environ.get("GL_VERBOSE"):
        import faulthandler
        faulthandler.dump_traceback(all_threads=True)
    cpu = (time.process_time() - cpu0) / (time.monotonic() - wall0)
    eng.stop()
    player.stdin.close()
    print(f"engine process CPU while listening: {cpu * 100:.0f}% of one core "
          f"({cpu * 100 / (os.cpu_count() or 1):.1f}% of the whole CPU)", flush=True)

    rows = []
    for lang, path, text, start, end in plays:
        # A sentence belongs to the clip it started in (the engine stamps every event).
        mine = [(t, e) for t, e in events if e.kind in ("partial", "final") and e.text
                and start - 0.5 <= e.speech_start <= end + 0.3]
        partials = [(t, e) for t, e in mine if e.kind == "partial"]
        finals = [(t, e) for t, e in mine if e.kind == "final"]
        row = {
            "clip": f"{lang}/{path.name}", "seconds": round(end - start, 2),
            "first_words": round(partials[0][0] - start, 2) if partials else None,
            "final_after_end": round(finals[-1][0] - end, 2) if finals else None,
            "heard": " | ".join(e.text for _, e in finals), "lang": " ".join(e.language for _, e in finals),
            "translation": " | ".join(e.translation for _, e in finals), "expected": text,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
