# PyInstaller recipe for the Clipper app folder (built by packaging/build.py).
# Two programs share one folder: Clipper.exe (the app, no console) and clipper-yt.exe (bundled yt-dlp).
from pathlib import Path

PK = Path(SPECPATH)
SRC = PK.parent
BUILD = PK / "build"
ICON = str(PK / "clipper.ico")

datas = [
    (str(SRC / "ui.html"), "."),
    (str(SRC / "pages"), "pages"),
    (str(SRC / "brand"), "brand"),
    (str(SRC / "fonts"), "fonts"),
    (str(SRC / "LICENSE"), "."),
    (str(SRC / "THIRD-PARTY-NOTICES.md"), "."),
]
binaries = [(str(BUILD / "bin" / "ffmpeg.exe"), "bin")]

UNUSED = ["matplotlib", "scipy", "pandas", "IPython", "jupyter", "PyQt5", "PyQt6", "PySide2", "PySide6",
          "pytest", "setuptools", "pip", "wheel", "pydoc_data", "lib2to3", "test", "unittest"]

app = Analysis(
    [str(PK / "clipper_main.py")],
    pathex=[str(SRC), str(PK)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["app", "clip", "live", "uninstall", "cryptography.hazmat.primitives.asymmetric.ed25519"],
    excludes=UNUSED + ["yt_dlp"],
    noarchive=False,
)
app_pyz = PYZ(app.pure)
app_exe = EXE(app_pyz, app.scripts, [], exclude_binaries=True, name="Clipper", icon=ICON,
              console=False, upx=False)

yt = Analysis(
    [str(PK / "yt_main.py")],
    pathex=[str(PK)],
    excludes=UNUSED + ["numpy", "PIL", "tkinter", "imageio_ffmpeg"],
    noarchive=False,
)
yt_pyz = PYZ(yt.pure)
yt_exe = EXE(yt_pyz, yt.scripts, [], exclude_binaries=True, name="clipper-yt", icon=ICON,
             console=True, upx=False)

coll = COLLECT(app_exe, app.binaries, app.datas, yt_exe, yt.binaries, yt.datas,
               name="Clipper", upx=False)
