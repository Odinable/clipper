# Third-party software in Clipper

Clipper's own code is under the licence in `LICENSE`. The Windows installer also bundles the open-source software below, each under its own licence.

| Software | Used for | Licence | Source |
|---|---|---|---|
| **FFmpeg** (`bin/ffmpeg.exe`, from the imageio-ffmpeg build) | Reading streams and cutting reels | GNU GPL v3 (this build includes GPL components such as x264) | https://ffmpeg.org · build scripts: https://github.com/imageio/imageio-binaries |
| **yt-dlp** (`clipper-yt.exe`) | Reading YouTube streams and chat | The Unlicense (public domain) | https://github.com/yt-dlp/yt-dlp |
| **Python** | Runs the app | PSF License | https://www.python.org |
| **NumPy** | Audio analysis | BSD 3-Clause | https://numpy.org |
| **Pillow** | Drawing captions | MIT-CMU (HPND) | https://python-pillow.org |
| **cryptography** | Checking the sign-in pass | Apache 2.0 / BSD | https://cryptography.io |
| **imageio-ffmpeg** | Provides the FFmpeg build | BSD 2-Clause | https://github.com/imageio/imageio-ffmpeg |
| **Tcl/Tk** | File picker and installer window | Tcl/Tk License (BSD-style) | https://www.tcl.tk |
| **PyInstaller** bootloader | Packs the app into an .exe | GPL v2 with a bootloader exception that allows any licence | https://pyinstaller.org |
| **Inter** font | App text | SIL Open Font License 1.1 | https://rsms.me/inter |
| **Space Grotesk** font | Headings and captions | SIL Open Font License 1.1 | https://github.com/floriankarsten/space-grotesk |

## FFmpeg (GPL v3)

FFmpeg is a separate program that Clipper runs. It isn't linked into Clipper's code. Under the GPL you're entitled to its source code: get it from https://ffmpeg.org/download.html, or see the exact build configuration at https://github.com/imageio/imageio-binaries. The full licence text is at https://www.gnu.org/licenses/gpl-3.0.txt.

## Not covered by the open-source licence

The Growthforge name, logo and "G" mark in `brand/` are trademarks of Growthforge. They're included so the app looks right, but they aren't licensed for reuse in other projects.
