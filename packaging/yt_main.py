"""clipper-yt.exe: the bundled yt-dlp, run by Clipper as a separate process.

It's a console program (so yt-dlp can print the stream address back to Clipper), but Clipper
starts it with no window, so nothing flashes on screen.
"""
import sys

import yt_dlp

if __name__ == "__main__":
    sys.exit(yt_dlp.main())
