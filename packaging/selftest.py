"""Checks a packaged build before it ships; not part of the installer.

    glosslane-selftest.exe <speech.wav> [cpu|cuda]

Loads the speech model, the translator and the VAD exactly as the app does,
then transcribes and translates the clip and lists the audio sources.
"""

import sys
import time


def main() -> int:
    from glosslane import audio, models
    from glosslane.audio import RATE
    from glosslane.config import Settings
    from glosslane.engine import Transcriber, Vad, pick_asr
    from glosslane.translate import make_translator
    from faster_whisper.audio import decode_audio

    device = sys.argv[2] if len(sys.argv) > 2 else "cpu"
    models.add_cuda_dirs()
    s = Settings(device=device)
    clip = decode_audio(sys.argv[1], RATE)
    vad = Vad()
    speech = sum(vad(clip[i:i + 512]) > 0.5 for i in range(0, len(clip) - 512, 512))
    print(f"vad: {speech} speech frames of {len(clip) // 512}")
    t = time.time()
    asr = Transcriber(pick_asr(s, device), device, 4)
    tr = make_translator(s, device, 4)
    print(f"models loaded on {device} in {time.time() - t:.1f} s")
    asr(clip[:RATE], "en")
    t = time.time()
    text, lang, _, _ = asr(clip, None, final=True)
    asr_s = time.time() - t
    t = time.time()
    out = tr.translate(text, lang, "ar", final=True)
    print(f"heard [{lang}] {text!r} in {asr_s:.2f} s")
    print(f"arabic: {ascii(out)} in {time.time() - t:.2f} s")
    print("devices:", [d.name for d in audio.list_devices()])
    print("apps:", [a.exe for a in audio.list_apps()])
    return 0 if text and out and speech else 1


if __name__ == "__main__":
    raise SystemExit(main())
