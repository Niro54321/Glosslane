# PyInstaller recipe, run by packaging/build.py. Makes dist/Glosslane with
# Glosslane.exe and glosslane-selftest.exe sharing one _internal folder; the
# self-test checks the build and is left out of the installer.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
BUILD = ROOT / "build"
DATAS = collect_data_files("faster_whisper")  # the Silero VAD model
BINARIES = collect_dynamic_libs("ctranslate2")
EXCLUDES = ["tkinter", "torch", "transformers", "nvidia", "matplotlib", "scipy", "pandas",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
            "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtDesigner", "PySide6.QtPdf"]


def analysis(script):
    return Analysis([str(ROOT / "packaging" / script)], pathex=[str(ROOT)], datas=DATAS, binaries=BINARIES,
                    hiddenimports=["comtypes.stream"], excludes=EXCLUDES, noarchive=False)


app = analysis("launcher.py")
test = analysis("selftest.py")

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="Glosslane",
              icon=str(BUILD / "glosslane.ico"), version=str(BUILD / "version.txt"),
              console=False, upx=False)
test_exe = EXE(PYZ(test.pure), test.scripts, [], exclude_binaries=True, name="glosslane-selftest",
               icon=str(BUILD / "glosslane.ico"), console=True, upx=False)

COLLECT(app_exe, app.binaries, app.datas, test_exe, test.binaries, test.datas,
        name="Glosslane", upx=False)
