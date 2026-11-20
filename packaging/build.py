"""Builds Clipper-Setup.exe.

    python packaging/build.py

Output: packaging/dist/Clipper-Setup.exe (upload this to the GitHub release).
Needs: pip install pyinstaller numpy pillow imageio-ffmpeg yt-dlp cryptography
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PK = Path(__file__).resolve().parent
BUILD, DIST = PK / "build", PK / "dist"


def run(*args):
    print(">", " ".join(map(str, args)))
    subprocess.run([sys.executable, "-m", "PyInstaller", *map(str, args)], check=True)


def main():
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    (BUILD / "bin").mkdir(parents=True)

    # 1. ffmpeg (the full build that ships with imageio-ffmpeg), under a plain name
    import imageio_ffmpeg
    shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), BUILD / "bin" / "ffmpeg.exe")

    # 2. the app folder: Clipper.exe + clipper-yt.exe + everything they need
    run(PK / "clipper.spec", "--noconfirm", "--distpath", DIST, "--workpath", BUILD / "work-app")
    app_dir = DIST / "Clipper"

    # 3. zip it as the installer's payload
    payload = BUILD / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(app_dir))

    # 4. the one-file installer with the payload inside
    run(PK / "setup.spec", "--noconfirm", "--distpath", DIST, "--workpath", BUILD / "work-setup")

    setup = DIST / "Clipper-Setup.exe"
    mb = lambda p: p.stat().st_size / 1e6
    folder = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1e6
    print(f"\nApp folder: {folder:.0f} MB  |  payload.zip: {mb(payload):.0f} MB  |  Clipper-Setup.exe: {mb(setup):.0f} MB")
    print(f"Installer: {setup}")


if __name__ == "__main__":
    main()
