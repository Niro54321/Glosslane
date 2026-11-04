"""Speech in, translated subtitles out.

The engine runs on one background thread:

  capture queue -> Silero VAD (32 ms frames) -> sentence buffer
     while someone talks: Whisper on the sentence so far, every ~0.4 s  -> partial
     after a short pause (or a long run of speech): Whisper once more  -> final
  each partial/final text -> translator -> on_event(...)

Whisper is not a streaming model, so "streaming" here means re-reading the
sentence while it grows. On a GPU that is cheap (tens of ms per pass); on a
CPU the partial passes are skipped whenever they would fall behind.
"""

from __future__ import annotations

import ctypes
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import models
from .audio import RATE, Capture
from .config import Settings
from .languages import from_whisper, to_whisper
from .translate import make_translator

FRAME = 512  # samples per VAD frame (32 ms)

# Whisper's well-known inventions on music, noise or silence. People do say
# some of these for real, so they are only dropped when Whisper is unsure.
HALLUCINATIONS = re.compile(
    r"^\W*(thanks? (you )?for watching( and see you next time)?|thank you\.?|you|bye\.?|subtitles? by.*"
    r"|.*amara\.org.*|ご視聴ありがとうございました|字幕.*|продолжение следует|sous-titr.*|untertitel.*"
    r"|♪+|\[.*\]|\(.*\))\W*$", re.I)


def _invented(seg) -> bool:
    if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8:
        return True
    text = seg.text.strip()
    return not re.search(r"\w", text) or (HALLUCINATIONS.match(text) is not None
                                          and (seg.no_speech_prob > 0.15 or seg.avg_logprob < -0.45))


@dataclass
class Event:
    kind: str                  # partial, final, status, error, ready
    text: str = ""             # what was said
    translation: str = ""
    language: str = ""
    speech_start: float = 0.0  # time.monotonic() when the sentence started, for latency
    speech_end: float = 0.0    # ... and of the newest audio in it
    extra: dict = field(default_factory=dict)


class Vad:
    """Silero VAD v6 (MIT), the ONNX file that ships with faster-whisper, one frame at a time."""

    def __init__(self):
        import onnxruntime
        from faster_whisper.utils import get_assets_path

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
                                                    providers=["CPUExecutionProvider"], sess_options=opts)
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.context = np.zeros(64, np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame])[None, :]
        prob, self.h, self.c = self.session.run(None, {"input": x, "h": self.h, "c": self.c})
        self.context = frame[-64:]
        return float(np.ravel(prob)[0])


def _below_normal_thread() -> None:
    try:
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except OSError:
        pass


def pick_device(settings: Settings) -> str:
    if settings.device in ("cuda", "cpu"):
        return settings.device
    if not models.gpu_pack_ready():
        return "cpu"
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001 (no driver, broken DLLs: just use the CPU)
        return "cpu"


def pick_asr(settings: Settings, device: str) -> str:
    if settings.asr_model != "auto":
        return settings.asr_model
    return "large-v3-turbo" if device == "cuda" else "base"


class Transcriber:
    def __init__(self, name: str, device: str, cpu_threads: int):
        from faster_whisper import WhisperModel

        model = models.WHISPER[name]
        path = models.download_model(model)
        # int8 weights on the GPU too: half the memory of float16, so no stalls
        # when a game or another app fills the graphics memory.
        compute = "int8_float16" if device == "cuda" else "int8"
        self.model = WhisperModel(str(path), device=device, compute_type=compute, cpu_threads=cpu_threads,
                                  num_workers=1)
        self.device = device
        self.sticky = ""  # the language of recent sentences, used when a short clip is ambiguous

    def __call__(self, audio: np.ndarray, language: str | None, final: bool = False, words: bool = False):
        """-> (text, language, segments, how sure the language is)"""
        lang = to_whisper(language) if language else None
        kwargs = dict(language=lang, beam_size=1, temperature=0.0, condition_on_previous_text=False,
                      without_timestamps=not words, word_timestamps=words, vad_filter=False,
                      no_speech_threshold=0.6, log_prob_threshold=-1.0, compression_ratio_threshold=2.4)
        segments, info = self.model.transcribe(audio, **kwargs)
        segments = list(segments)
        detected, sure = info.language, (1.0 if lang else info.language_probability)
        if lang is None and self.sticky and detected != self.sticky and (
                sure < 0.6 or (sure < 0.85 and len(audio) < 2.5 * 16000)):
            # Too short or too unclear to tell; keep the language people were just speaking.
            detected = self.sticky
            segments, info = self.model.transcribe(audio, **{**kwargs, "language": detected})
            segments = list(segments)
        if lang is None and final and sure >= 0.7 and len(audio) >= 16000:
            self.sticky = detected
        kept = [s for s in segments if not _invented(s)]
        text = " ".join(s.text.strip() for s in kept).strip()
        if _looping(text):
            text, kept = "", []
        return text, from_whisper(detected), kept, sure


def _looping(text: str) -> bool:
    words = text.lower().split()
    return len(words) >= 8 and len(set(words)) <= len(words) // 4


class Engine:
    """Owns the capture, the models and the worker thread."""

    PARTIAL_EVERY = 0.4      # s between partial passes while someone talks
    MIN_PARTIAL = 0.6        # s of speech before the first partial
    PREROLL = 0.25           # s of audio kept from before the VAD fires
    START, KEEP = 0.5, 0.3   # VAD probability to start speech / to count as still speaking
    MAX_BEHIND = 5.0         # s of unprocessed audio before skipping ahead

    def __init__(self, settings: Settings, on_event: Callable[[Event], None]):
        self.s = settings
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.capture: Capture | None = None
        self.info: dict = {}
        self._new_source = False
        self.paused = False  # subtitles off: keep listening, skip the work

    # -- lifecycle --

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._main, name="glosslane-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.capture:
            self.capture.stop()
        if self._thread:
            self._thread.join(5)

    def change_source(self) -> None:
        """Listen to settings.source_mode/device_id/app_exe again, without reloading models."""
        self._new_source = True

    @property
    def level(self) -> float:
        return self.capture.level if self.capture else 0.0

    def _emit(self, kind: str, **kw) -> None:
        try:
            self.on_event(Event(kind, **kw))
        except Exception:  # noqa: BLE001 (the UI must never kill the engine)
            pass

    def _main(self) -> None:
        if self.s.low_impact:
            _below_normal_thread()
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001
            self._emit("error", text=f"Couldn't load the models: {exc}")
            return
        self.capture = Capture(self.s.source_mode, self.s.device_id, self.s.app_exe)
        self.capture.start()
        self._emit("ready", extra=dict(self.info))
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001
            self._emit("error", text=f"Stopped: {exc}")

    def _load(self) -> None:
        last = [0.0]

        def progress(done: int, total: int) -> None:
            if time.monotonic() - last[0] > 0.5 or done >= total:
                last[0] = time.monotonic()
                self._emit("status", text=f"Downloading models… {done / 1e6:.0f} of {total / 1e6:.0f} MB",
                           extra={"done": done, "total": total})
        models.progress_hook = progress
        models.add_cuda_dirs()
        device = pick_device(self.s)
        cores = os.cpu_count() or 4
        threads = max(2, cores // 4) if self.s.low_impact else max(2, cores // 2)
        name = pick_asr(self.s, device)
        self._emit("status", text=f"Loading speech model ({name}) on {device.upper()}…")
        try:
            self.asr = Transcriber(name, device, threads)
        except Exception:  # noqa: BLE001 (CUDA present but unusable: fall back to the CPU)
            if device != "cuda":
                raise
            device, name = "cpu", ("base" if self.s.asr_model == "auto" else name)
            self._emit("status", text=f"GPU failed, loading {name} on CPU…")
            self.asr = Transcriber(name, device, threads)
        self._emit("status", text="Loading translator…")
        self.translator = make_translator(self.s, device, threads)
        self.vad = Vad()
        # The first pass on a GPU compiles kernels; do it now, not on the first sentence.
        self.asr(np.zeros(RATE, np.float32), "en")
        self.translator.translate("Hello.", "en", self.s.target_language, final=False)
        self.info = {"device": device, "asr": name, "translator": self.translator.name}

    # -- the loop --

    def _loop(self) -> None:
        q = self.capture.queue
        pending = np.zeros(0, np.float32)
        pre = np.zeros(0, np.float32)
        u: Utterance | None = None
        next_pass = 0.0

        while not self._stop.is_set():
            if self._new_source:
                self._new_source = False
                self.capture.stop()
                self.capture = Capture(self.s.source_mode, self.s.device_id, self.s.app_exe)
                self.capture.start()
                q, u, pending, pre = self.capture.queue, None, np.zeros(0, np.float32), np.zeros(0, np.float32)
                self.vad.reset()
            forced = None if self.s.source_language == "auto" else self.s.source_language
            sil_frames = max(3, int(self.s.silence_ms / 1000 * RATE / FRAME))
            max_len = int(self.s.max_sentence * RATE)

            got = []
            try:
                got.append(q.get(timeout=0.05))
                while True:
                    got.append(q.get_nowait())
            except queue.Empty:
                pass
            if self.paused:
                u, pending = None, np.zeros(0, np.float32)
                continue
            behind = sum(len(c) for c, _ in got) / RATE
            if behind > self.MAX_BEHIND:  # a stall (a game grabbed the GPU): skip ahead, don't lag forever
                keep, total = [], 0
                for c, t in reversed(got):
                    if total >= RATE:
                        break
                    keep.insert(0, (c, t))
                    total += len(c)
                got, u, pending = keep, None, np.zeros(0, np.float32)
                self.vad.reset()
                self._emit("status", text=f"Busy PC: skipped {behind - 1:.1f} s of audio to keep up")

            for chunk, t in got:
                pending = np.concatenate([pending, chunk])
                n = len(pending) // FRAME
                for i in range(n):
                    frame = pending[i * FRAME:(i + 1) * FRAME]
                    frame_t = t - (len(pending) - (i + 1) * FRAME) / RATE
                    p = self.vad(frame)
                    if u is None:
                        pre = np.concatenate([pre, frame])[-int(self.PREROLL * RATE):]
                        if p >= self.START:
                            u = Utterance(pre.copy(), frame_t - len(pre) / RATE, forced)
                            pre = np.zeros(0, np.float32)
                        continue
                    u.add(frame, p >= self.KEEP, p >= self.START, frame_t)
                    if u.silence >= sil_frames:
                        self._finish(u)
                        u = None
                        self.vad.reset()
                    elif u.length >= max_len:
                        u = self._cut(u)
                pending = pending[n * FRAME:]

            self.current = u  # for tests and the debugger
            # Read the sentence again while it grows, and once more as soon as a
            # pause starts: when the pause turns out to end the sentence, the
            # final text is then already there.
            if u is None:
                continue
            now = time.monotonic()
            pause_started = u.silence >= 2 and u.covered < u.speech_len
            growing = (self.s.partials and u.length >= self.MIN_PARTIAL * RATE and now >= next_pass
                       and u.silence < 2)
            if (pause_started or growing) and u.voiced >= 4:
                began = time.monotonic()
                self._read(u, final=pause_started)
                took = time.monotonic() - began
                # A slow or busy machine reads less often instead of falling behind.
                next_pass = time.monotonic() + max(self.PARTIAL_EVERY, took)


    def _read(self, u: "Utterance", final: bool) -> None:
        """One Whisper pass over the sentence so far; shows it as a partial."""
        audio = u.audio()
        text, lang, _, sure = self.asr(audio, u.lang, final=final)
        u.covered = u.speech_len
        if u.lang is None and sure >= 0.6 and len(audio) >= RATE:
            u.lang = lang  # settle the language once per sentence: no flip-flopping
        if not text:
            u.text = u.tr = ""
            return
        if text != u.text:
            u.text, u.detected = text, lang
            if self.s.partials:
                u.tr = self.translator.translate(text, lang, self.s.target_language, final=False)
                self._emit("partial", text=text, translation=u.tr, language=lang,
                           speech_start=u.start_t, speech_end=u.end_t)

    def _finish(self, u: "Utterance") -> None:
        if u.voiced < 6:  # under ~0.2 s of clear speech: a click, a cough, a beat
            self._emit("final", speech_start=u.start_t, speech_end=u.end_t)
            return
        if u.covered < u.speech_len:  # the last read missed some speech: read it all now
            self._read(u, final=True)
        if not u.text:
            self._emit("final", speech_start=u.start_t, speech_end=u.end_t)  # clears a stale partial
            return
        tr = self.translator.translate(u.text, u.detected, self.s.target_language, final=True)
        self._emit("final", text=u.text, translation=tr, language=u.detected,
                   speech_start=u.start_t, speech_end=u.end_t)

    def _cut(self, u: "Utterance") -> "Utterance":
        """A long run of speech with no pause: show what is settled, keep the rest."""
        audio = u.audio()
        text, lang, segments, sure = self.asr(audio, u.lang, final=True, words=True)
        settled_lang = u.lang or (lang if sure >= 0.6 else None)
        words = [w for s in segments for w in (s.words or [])]
        limit = len(audio) / RATE - 1.2
        settled = [w for w in words if w.end <= limit]
        # Prefer to cut after a sentence end, if there is one in the settled part.
        for i in range(len(settled) - 1, 0, -1):
            if settled[i].word.strip()[-1:] in ".?!,;:。？！，":
                settled = settled[:i + 1]
                break
        if not settled:
            if text:
                tr = self.translator.translate(text, lang, self.s.target_language, final=True)
                self._emit("final", text=text, translation=tr, language=lang,
                           speech_start=u.start_t, speech_end=u.end_t)
            return Utterance(np.zeros(0, np.float32), u.end_t, settled_lang)
        said = "".join(w.word for w in settled).strip()
        if said:
            tr = self.translator.translate(said, lang, self.s.target_language, final=True)
            self._emit("final", text=said, translation=tr, language=lang,
                       speech_start=u.start_t, speech_end=u.start_t + settled[-1].end)
        cut = int(settled[-1].end * RATE)
        rest = Utterance(audio[cut:], u.start_t + cut / RATE, settled_lang)
        rest.voiced, rest.end_t = 4, u.end_t
        return rest


class Utterance:
    """One sentence being spoken: its audio and what has been read from it."""

    def __init__(self, preroll: np.ndarray, start_t: float, lang: str | None):
        self.parts = [preroll]
        self.length = len(preroll)
        self.start_t = self.end_t = start_t
        self.lang = lang                 # forced or settled source language
        self.silence = 0                 # quiet frames in a row
        self.voiced = 0                  # frames with clear speech
        self.speech_len = len(preroll)   # samples up to the newest speech frame
        self.covered = 0                 # speech_len when the newest read was made
        self.text = self.tr = self.detected = ""

    def add(self, frame: np.ndarray, speaking: bool, clear: bool, t: float) -> None:
        self.parts.append(frame)
        self.length += len(frame)
        if speaking:
            self.silence, self.end_t, self.speech_len = 0, t, self.length
        else:
            self.silence += 1
        self.voiced += clear

    def audio(self) -> np.ndarray:
        if len(self.parts) > 1:
            self.parts = [np.concatenate(self.parts)]
        # Whisper only needs a little of the pause after the last word.
        return self.parts[0][:self.speech_len + int(0.15 * RATE)]


def engine_log(path: Path):
    """For tests: an on_event that appends JSON lines with timings."""
    import json

    lock = threading.Lock()

    def write(ev: Event) -> None:
        with lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.monotonic(), **ev.__dict__}, ensure_ascii=False) + "\n")
    return write
