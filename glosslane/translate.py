"""Text translation: local CTranslate2 models, or an optional online server.

- M2M100 (MIT): 100 languages, any to any. 418M is fast enough for a CPU.
- MADLAD-400 3B (Apache-2.0): better, especially into Arabic; wants a GPU.
- Online: a LibreTranslate-compatible server the user chooses. Off unless
  picked, because the text leaves the PC.
"""

from __future__ import annotations

import json
import urllib.request
from collections import OrderedDict

from . import models
from .config import Settings


class Translator:
    name = "none"

    def translate(self, text: str, source: str, target: str, final: bool) -> str:
        return ""


class _Cached(Translator):
    def __init__(self):
        self._cache: OrderedDict = OrderedDict()

    def translate(self, text: str, source: str, target: str, final: bool) -> str:
        if not text.strip():
            return ""
        if source == target:
            return text
        key = (text, source, target)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        out = self._run(text, source, target, final)
        self._cache[key] = out
        if len(self._cache) > 300:
            self._cache.popitem(last=False)
        return out

    def _run(self, text: str, source: str, target: str, final: bool) -> str:
        raise NotImplementedError


class M2M100(_Cached):
    def __init__(self, key: str, device: str, threads: int):
        super().__init__()
        import ctranslate2
        import sentencepiece

        self.name = key
        path = models.download_model(models.TRANSLATORS[key])
        self.sp = sentencepiece.SentencePieceProcessor(model_file=str(path / "sentencepiece.bpe.model"))
        compute = "int8_float16" if device == "cuda" else "int8"
        self.model = ctranslate2.Translator(str(path), device=device, compute_type=compute,
                                            inter_threads=1, intra_threads=threads)
        self.vocab = set((path / "shared_vocabulary.txt").read_text(encoding="utf-8").split())

    def _lang(self, code: str) -> str:
        token = f"__{code}__"
        return token if token in self.vocab else "__en__"

    def _run(self, text: str, source: str, target: str, final: bool) -> str:
        tokens = [self._lang(source)] + self.sp.encode(text, out_type=str) + ["</s>"]
        result = self.model.translate_batch([tokens], target_prefix=[[self._lang(target)]],
                                            beam_size=(3 if final else 1), max_decoding_length=200,
                                            repetition_penalty=1.1)
        out = result[0].hypotheses[0][1:]
        return self.sp.decode(out).strip()


class Madlad(_Cached):
    name = "madlad400_3b"

    def __init__(self, device: str, threads: int):
        super().__init__()
        import ctranslate2
        import sentencepiece

        path = models.download_model(models.TRANSLATORS["madlad400_3b"])
        self.sp = sentencepiece.SentencePieceProcessor(model_file=str(path / "spiece.model"))
        compute = "int8_float16" if device == "cuda" else "int8"
        self.model = ctranslate2.Translator(str(path), device=device, compute_type=compute,
                                            inter_threads=1, intra_threads=threads)

    def _run(self, text: str, source: str, target: str, final: bool) -> str:
        tokens = self.sp.encode(f"<2{target}> {text}", out_type=str) + ["</s>"]
        result = self.model.translate_batch([tokens], beam_size=(2 if final else 1), max_decoding_length=200,
                                            repetition_penalty=1.1)
        return self.sp.decode(result[0].hypotheses[0]).strip()


class Online(_Cached):
    """POST /translate on a LibreTranslate-compatible server."""

    name = "online"

    def __init__(self, url: str, key: str):
        super().__init__()
        if not url.startswith(("https://", "http://")):
            raise ValueError("Set the online translator's address in Settings")
        self.url = url.rstrip("/") + ("" if url.rstrip("/").endswith("/translate") else "/translate")
        self.key = key

    def _run(self, text: str, source: str, target: str, final: bool) -> str:
        if not final:
            return ""  # only whole sentences go online
        body = {"q": text, "source": source or "auto", "target": target, "format": "text"}
        if self.key:
            body["api_key"] = self.key
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r).get("translatedText", "")


def make_translator(s: Settings, device: str, threads: int) -> Translator:
    if s.translator == "none":
        return Translator()
    if s.translator == "online":
        return Online(s.online_url, s.online_key)
    if s.translator == "madlad400_3b":
        return Madlad(device, threads)
    return M2M100(s.translator if s.translator in models.TRANSLATORS else "m2m100_418M", device, threads)
