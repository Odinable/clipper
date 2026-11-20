"""Entry point for the installed Clipper.exe.

  Clipper.exe              open the app
  Clipper.exe --uninstall  remove Clipper (used by Windows "Apps > Installed apps")
"""
import os
import sys

if getattr(sys, "frozen", False):
    # Bundled modules (app.py, clip.py, live.py) live next to the exe's _internal folder.
    sys.path.insert(0, getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))

if "--uninstall" in sys.argv:
    import uninstall
    uninstall.run()
else:
    import app
    app.main()
