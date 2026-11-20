# PyInstaller recipe for Clipper-Setup.exe: one file with the zipped app inside (built by packaging/build.py).
from pathlib import Path

PK = Path(SPECPATH)
ICON = str(PK / "clipper.ico")

a = Analysis(
    [str(PK / "setup_main.py")],
    pathex=[str(PK)],
    datas=[(str(PK / "build" / "payload.zip"), "."), (ICON, "."), (str(PK / "setup-mark.png"), ".")],
    excludes=["numpy", "PIL", "cryptography", "yt_dlp", "imageio_ffmpeg", "matplotlib", "scipy", "pandas",
              "pytest", "setuptools", "pip", "unittest", "pydoc_data", "lib2to3", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Clipper-Setup", icon=ICON,
          console=False, upx=False, runtime_tmpdir=None)
