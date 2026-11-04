"""Engine loop, hotkeys and settings, with no sound card and no big models.

The VAD is the real Silero model; Whisper and the translator are stand-ins
that report what audio they were given, so the timing logic can be checked
exactly.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

import numpy as np
import pytest

from glosslane import config, engine
from glosslane.audio import RATE
from glosslane.config import Settings
from glosslane.hotkey import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, parse


SENTENCES = [
    "Thank you for subscribing to the channel.",
    "Today we are going to build a gaming computer from scratch, and I will show you every single step "
    "of the way without stopping for a break at all.",
]


@pytest.fixture(scope="session")
def voice(tmp_path_factory):
    """Real speech, made with Windows' text-to-speech straight into a file (nothing plays)."""
    from faster_whisper.audio import decode_audio

    out = []
    for i, text in enumerate(SENTENCES):
        wav = tmp_path_factory.mktemp("tts") / f"s{i}.wav"
        script = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate=1; $s.SetOutputToWaveFile('{wav}'); $s.Speak('{text}'); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        audio = decode_audio(str(wav), RATE)
        nz = np.flatnonzero(np.abs(audio) > 0.01)
        out.append(audio[nz[0]:nz[-1]].astype(np.float32))
    return out


class FakeCapture:
    def __init__(self, *a, **k):
        self.queue: queue.Queue = queue.Queue()
        self.level = 0.0
        self.error = ""

    def start(self):
        pass

    def stop(self):
        pass


class FakeAsr:
    def __init__(self, delay: float = 0.0):
        self.calls: list[float] = []
        self.delay = delay

    def __call__(self, audio, language, final=False, words=False, strict=False):
        self.calls.append(len(audio) / RATE)
        time.sleep(self.delay)
        return f"said {len(audio) / RATE:.1f}", (language or "en"), [], 0.99


class FakeTranslator:
    name = "fake"

    def translate(self, text, source, target, final):
        return f"[{target}] {text}"


def run(audio_parts, settings: Settings | None = None, delay: float = 0.0, realtime: bool = True):
    """Feed audio as 10 ms packets, the way WASAPI does, and collect events."""
    events = []
    s = settings or Settings()
    eng = engine.Engine(s, lambda ev: events.append((time.monotonic(), ev)))
    eng.asr, eng.translator, eng.vad = FakeAsr(delay), FakeTranslator(), engine.Vad()
    eng.capture = FakeCapture()
    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    audio = np.concatenate(audio_parts)
    step = RATE // 100
    for i in range(0, len(audio), step):
        eng.capture.queue.put((audio[i:i + step], time.monotonic()))
        if realtime:
            time.sleep(0.01)
    time.sleep(1.0)
    eng._stop.set()
    t.join(3)
    return eng, events


def finals(events):
    return [e for _, e in events if e.kind == "final" and e.text]


def test_vad_hears_speech_not_silence(voice):
    vad = engine.Vad()
    x = voice[0]
    probs = [vad(x[i:i + 512]) for i in range(0, len(x) - 512, 512)]
    assert np.mean(np.array(probs[5:]) > 0.5) > 0.8
    vad.reset()
    quiet = np.zeros(RATE, np.float32)
    assert max(vad(quiet[i:i + 512]) for i in range(0, RATE - 512, 512)) < 0.2


def test_one_sentence_gives_partials_then_one_final_soon_after_the_pause(voice):
    silence = np.zeros(int(1.2 * RATE), np.float32)
    eng, events = run([silence, voice[0], silence])
    got = finals(events)
    assert len(got) == 1
    assert got[0].translation.startswith("[ar] said")
    assert any(e.kind == "partial" for _, e in events)
    # The final arrives within the pause length plus a little.
    shown = next(t for t, e in events if e.kind == "final" and e.text)
    assert shown - got[0].speech_end < Settings().silence_ms / 1000 + 0.35


def test_two_sentences_are_kept_apart(voice):
    gap = np.zeros(int(1.0 * RATE), np.float32)
    _, events = run([gap, voice[0], gap, voice[1], gap])
    assert len(finals(events)) == 2


def test_long_speech_is_cut_and_nothing_is_lost(voice):
    s = Settings(max_sentence=3.0, silence_ms=900)  # TTS pauses briefly at commas
    eng, events = run([np.zeros(RATE // 2, np.float32), voice[1], np.zeros(2 * RATE, np.float32)], s)
    assert len([e for _, e in events if e.kind == "final"]) >= 2


def test_a_click_is_not_a_sentence():
    click = np.zeros(int(1.5 * RATE), np.float32)
    click[RATE // 2:RATE // 2 + 400] = 0.8
    _, events = run([click])
    assert not finals(events)


def test_a_slow_machine_reads_less_often_but_keeps_up(voice):
    silence = np.zeros(int(1.5 * RATE), np.float32)
    fast, _ = run([silence, voice[1], silence])
    slow, events = run([silence, voice[1], silence], delay=0.35)
    assert finals(events)
    assert len(slow.asr.calls) < len(fast.asr.calls)


def test_skips_ahead_after_a_long_stall(voice):
    # 7 s arrive at once (the PC froze): the engine drops what it can't use.
    _, events = run([voice[1], voice[0], np.zeros(2 * RATE, np.float32)], realtime=False)
    assert any(e.kind == "status" and "skipped" in e.text for _, e in events)


def test_songs_mode_reads_steady_sound_the_speech_detector_misses():
    # A loud tone stands in for singing over music: Silero ignores it.
    t = np.arange(4 * RATE) / RATE
    tone = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    quiet = np.zeros(RATE, np.float32)
    _, off = run([quiet, tone, quiet], Settings(songs=False))
    eng, on = run([quiet, tone, quiet], Settings(songs=True))
    assert not finals(off)
    assert finals(on) and eng.asr.calls


def test_paused_does_no_work():
    eng = engine.Engine(Settings(), lambda ev: None)
    eng.asr, eng.translator, eng.vad, eng.capture = FakeAsr(), FakeTranslator(), engine.Vad(), FakeCapture()
    eng.paused = True
    t = threading.Thread(target=eng._loop, daemon=True)
    t.start()
    for i in range(0, 3 * RATE, 160):
        eng.capture.queue.put((np.full(160, 0.3, np.float32), time.monotonic()))
    time.sleep(0.5)
    eng._stop.set()
    t.join(3)
    assert eng.asr.calls == []


@pytest.mark.parametrize("text,expected", [
    ("Ctrl+Alt+S", (MOD_NOREPEAT | MOD_CONTROL | MOD_ALT, ord("S"))),
    ("Ctrl+Shift+F9", (MOD_NOREPEAT | MOD_CONTROL | MOD_SHIFT, 0x78)),
    ("Alt+1", (MOD_NOREPEAT | MOD_ALT, ord("1"))),
])
def test_hotkey_parse(text, expected):
    assert parse(text) == expected


def test_hallucination_filter_keeps_real_thanks():
    class Seg:
        def __init__(self, text, nsp, lp):
            self.text, self.no_speech_prob, self.avg_logprob = text, nsp, lp
    assert engine._invented(Seg(" Thank you.", 0.4, -0.7))
    assert not engine._invented(Seg(" Thank you.", 0.01, -0.2))
    assert engine._invented(Seg(" Subtitles by the Amara.org community", 0.3, -0.5))
    assert not engine._invented(Seg(" Thank you for subscribing to the channel.", 0.05, -0.3))
    assert engine._looping("go go go go go go go go go go")


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    s = Settings(target_language="fr", font_size=40, app_exe="Discord.exe")
    s.save()
    assert Settings.load() == s
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    data["from_a_newer_version"] = 1
    (tmp_path / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    assert Settings.load().target_language == "fr"
