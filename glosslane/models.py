"""Models and the GPU pack: where they live, and downloading them once.

Everything is downloaded to %LOCALAPPDATA%\\Glosslane, from Hugging Face
(models) and PyPI (NVIDIA's CUDA libraries). After that the app works offline;
nothing is ever uploaded.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import GPU_DIR, MODELS_DIR

Progress = Callable[[int, int], None]  # bytes done, bytes total
progress_hook: Progress | None = None  # used when a download is given no callback


@dataclass(frozen=True)
class Model:
    key: str
    repo: str
    label: str
    licence: str
    size_mb: int


WHISPER = {
    "tiny": Model("tiny", "Systran/faster-whisper-tiny", "Tiny (fastest, rough)", "MIT", 75),
    "base": Model("base", "Systran/faster-whisper-base", "Base (fast on CPU)", "MIT", 145),
    "small": Model("small", "Systran/faster-whisper-small", "Small (better, slow on CPU)", "MIT", 485),
    "medium": Model("medium", "Systran/faster-whisper-medium", "Medium", "MIT", 1530),
    "large-v3-turbo": Model("large-v3-turbo", "dropbox-dash/faster-whisper-large-v3-turbo",
                            "Large v3 Turbo (best on GPU)", "MIT", 1620),
    "large-v3": Model("large-v3", "Systran/faster-whisper-large-v3", "Large v3 (slow)", "MIT", 3090),
}

TRANSLATORS = {
    "m2m100_418M": Model("m2m100_418M", "michaelfeil/ct2fast-m2m100_418M", "M2M100 418M (fast, good)", "MIT", 980),
    "madlad400_3b": Model("madlad400_3b", "Nextcloud-AI/madlad400-3b-mt-ct2-int8",
                          "MADLAD-400 3B (best quality, slower, GPU)", "Apache-2.0", 2980),
}

# NVIDIA's CUDA libraries that CTranslate2 needs on the GPU, straight from their PyPI wheels.
GPU_WHEELS = [("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.27.0.42")]
GPU_SKIP = {"nvblas64_12.dll"}


def model_dir(model: Model) -> Path:
    return MODELS_DIR / model.repo.replace("/", "--")


def is_downloaded(model: Model) -> bool:
    return (model_dir(model) / ".complete").is_file()


def _hf_files(repo: str) -> list[tuple[str, int]]:
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=30) as r:
        info = json.load(r)
    return [(s["rfilename"], s.get("size") or 0) for s in info["siblings"]
            if not s["rfilename"].startswith(".") and not s["rfilename"].lower().startswith("readme")]


def _fetch(url: str, dest: Path, done: int, total: int, progress: Progress | None, cancel) -> int:
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Glosslane"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            if cancel and cancel():
                raise InterruptedError("cancelled")
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            done += len(block)
            if progress:
                progress(done, total)
    tmp.replace(dest)
    return done


def download_model(model: Model, progress: Progress | None = None, cancel=None) -> Path:
    target = model_dir(model)
    if is_downloaded(model):
        return target
    progress = progress or progress_hook
    target.mkdir(parents=True, exist_ok=True)
    files = _hf_files(model.repo)
    total, done = sum(size for _, size in files), 0
    for name, size in files:
        dest = target / name
        if dest.is_file() and dest.stat().st_size == size:
            done += size
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        done = _fetch(f"https://huggingface.co/{model.repo}/resolve/main/{name}", dest, done, total, progress, cancel)
    (target / ".complete").write_text(model.repo)
    return target


# --- GPU -----------------------------------------------------------------------

def _dev_cuda_dirs() -> list[str]:
    """When running from source, the CUDA wheels installed in the venv."""
    if getattr(sys, "frozen", False):
        return []
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    return glob.glob(str(base / "*" / "bin"))


def gpu_pack_ready() -> bool:
    return bool(_dev_cuda_dirs()) or (GPU_DIR / ".complete").is_file()


def add_cuda_dirs() -> None:
    dirs = _dev_cuda_dirs() or ([str(GPU_DIR)] if gpu_pack_ready() else [])
    for d in dirs:
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def has_nvidia_gpu() -> bool:
    """An NVIDIA driver is installed (nvcuda.dll), whatever CTranslate2 thinks yet."""
    try:
        import ctypes

        ctypes.WinDLL("nvcuda.dll")
        return True
    except OSError:
        return False


def gpu_pack_size() -> int:
    total = 0
    for name, version in GPU_WHEELS:
        total += _wheel(name, version)[1]
    return total


def _wheel(name: str, version: str) -> tuple[str, int]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        info = json.load(r)
    for f in info["urls"]:
        if f["filename"].endswith("win_amd64.whl"):
            return f["url"], f["size"]
    raise OSError(f"No Windows build of {name} {version}")


def download_gpu_pack(progress: Progress | None = None, cancel=None) -> None:
    wheels = [_wheel(n, v) for n, v in GPU_WHEELS]
    total, done = sum(size for _, size in wheels), 0
    GPU_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="glosslane-gpu-") as tmp:
        for url, size in wheels:
            whl = Path(tmp) / url.rsplit("/", 1)[-1]
            done = _fetch(url, whl, done, total, progress, cancel)
            with zipfile.ZipFile(whl) as z:
                for info in z.infolist():
                    name = info.filename.rsplit("/", 1)[-1]
                    if "/bin/" in info.filename and name.lower().endswith(".dll") and name not in GPU_SKIP:
                        with z.open(info) as src, open(GPU_DIR / name, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                    elif name.upper().startswith("LICENSE") and info.filename.count("/") <= 2:
                        (GPU_DIR / "licenses").mkdir(exist_ok=True)
                        (GPU_DIR / "licenses" / f"{url.rsplit('/', 1)[-1].split('-')[0]}-{name}").write_bytes(z.read(info))
            whl.unlink()
    (GPU_DIR / ".complete").write_text("ok")
