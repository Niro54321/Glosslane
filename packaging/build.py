"""Builds the Windows installer.

    .venv\\Scripts\\python.exe packaging\\build.py [--skip-test]

1. icon, version info and third-party notices into build/
2. PyInstaller: dist/Glosslane (the app, plus a self-test exe)
3. the self-test: speech model, translator, VAD and audio sources in the packaged app
4. Inno Setup: dist/Glosslane-Setup-<version>.exe

Needs PyInstaller in the venv and Inno Setup 6 (winget install JRSoftware.InnoSetup).
Models and the GPU pack are not in the installer; the app downloads them once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD, DIST = ROOT / "build", ROOT / "dist"
sys.path.insert(0, str(ROOT))

from glosslane import __version__  # noqa: E402

# Tools that build or check the app, and the CUDA wheels (the app downloads those itself); none of them ship.
NOT_SHIPPED = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "pip",
               "setuptools", "packaging", "pyflakes", "pytest", "pluggy", "iniconfig", "pygments", "colorama",
               "glosslane", "nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12"}


def step(text: str) -> None:
    print(f"\n== {text}", flush=True)


def prepare() -> None:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtWidgets import QApplication

    from glosslane.ui import app_icon

    BUILD.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication(["build"])  # noqa: F841 (icons need Qt)
    icon = app_icon()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = []
    for s in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        icon.pixmap(QSize(s, s)).save(buf, "PNG")
        pngs.append(bytes(data))
    # An .ico is a small directory of PNG images.
    ico = b"\0\0\1\0" + len(sizes).to_bytes(2, "little")
    offset = 6 + 16 * len(sizes)
    for s, png in zip(sizes, pngs):
        ico += bytes([s % 256, s % 256, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
        ico += len(png).to_bytes(4, "little") + offset.to_bytes(4, "little")
        offset += len(png)
    (BUILD / "glosslane.ico").write_bytes(ico + b"".join(pngs))
    nums = ", ".join((__version__.split(".") + ["0"] * 4)[:4])
    (BUILD / "version.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums})),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Niro54321'),
    StringStruct('FileDescription', 'Glosslane'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('LegalCopyright', 'MIT License'),
    StringStruct('ProductName', 'Glosslane'),
    StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")


def notices(target: Path) -> None:
    """THIRD-PARTY-NOTICES.txt and every bundled package's own licence files."""
    licenses = target / "licenses"
    shutil.rmtree(licenses, ignore_errors=True)
    lines = ["Glosslane is MIT licensed (see LICENSE). The installer also contains the",
             "open-source software below, each under its own licence; the full texts are",
             "in the licenses folder next to this file.", "",
             f"Python {sys.version.split()[0]}  PSF-2.0  https://www.python.org/"]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        (licenses / "Python").mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, licenses / "Python" / "LICENSE.txt")
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() in NOT_SHIPPED:
            continue
        licence = dist.metadata.get("License-Expression") or next(
            (c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License ::")),
            (dist.metadata.get("License") or "see licence file").splitlines()[0][:60])
        url = next((u.split(",", 1)[-1].strip() for u in dist.metadata.get_all("Project-URL") or []
                    if u.lower().startswith(("source", "repository", "homepage", "code"))),
                   dist.metadata.get("Home-page") or "")
        lines.append(f"{name} {dist.version}  {licence}  {url}".rstrip())
        for f in dist.files or []:
            if any(part.lower().startswith(("license", "licence", "copying", "notice", "authors"))
                   for part in Path(f).parts[-1:]) or "licenses" in Path(f).parts:
                src = Path(dist.locate_file(f))
                if src.is_file():
                    dest = licenses / name / Path(f).name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
    lines += ["",
              "Bundled inside the packages above:",
              "- Qt 6 (inside PySide6), LGPL-3.0; source: https://download.qt.io/official_releases/qt/",
              "- Silero VAD v6 model (inside faster-whisper), MIT, https://github.com/snakers4/silero-vad",
              "- FFmpeg libraries (inside PyAV, used by faster-whisper), LGPL-2.1+, https://ffmpeg.org/",
              "",
              "Downloaded by the app on first use (not in the installer), each under its own licence:",
              "- Whisper speech models converted for CTranslate2 (MIT, OpenAI):",
              "  Systran/faster-whisper-*, dropbox-dash/faster-whisper-large-v3-turbo on Hugging Face",
              "- M2M100 418M translation model (MIT, Meta AI): michaelfeil/ct2fast-m2m100_418M",
              "- MADLAD-400 3B translation model (Apache-2.0, Google): Nextcloud-AI/madlad400-3b-mt-ct2-int8",
              "- Optional GPU pack: NVIDIA cuBLAS and cuDNN for CUDA 12, from NVIDIA's PyPI wheels,",
              "  under NVIDIA's own licence (saved next to the DLLs)."]
    (target / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def pyinstaller() -> Path:
    shutil.rmtree(DIST / "Glosslane", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
                    str(ROOT / "packaging" / "glosslane.spec")], check=True)
    out = DIST / "Glosslane"
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE.txt")
    notices(out)
    return out


def selftest(out: Path) -> None:
    wav = Path(tempfile.mkdtemp(prefix="glosslane-selftest-")) / "speech.wav"
    script = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('Thank you for subscribing to the channel.'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    for device in ("cpu", "cuda"):
        subprocess.run([str(out / "glosslane-selftest.exe"), str(wav), device], check=True)


def inno() -> Path:
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
                  Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6/ISCC.exe"]
    iscc = next((c for c in candidates if c.is_file()), None) or shutil.which("ISCC")
    if not iscc:
        sys.exit("Inno Setup 6 not found: winget install JRSoftware.InnoSetup")
    subprocess.run([str(iscc), f"/DAppVersion={__version__}", str(ROOT / "packaging" / "glosslane.iss")],
                   check=True)
    return DIST / f"Glosslane-Setup-{__version__}.exe"


def main() -> None:
    step(f"Glosslane {__version__}: icon and version info")
    prepare()
    step("PyInstaller")
    out = pyinstaller()
    if "--skip-test" not in sys.argv:
        step("Self-test in the packaged app")
        selftest(out)
    step("Inno Setup")
    setup = inno()
    print(f"\nDone: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
