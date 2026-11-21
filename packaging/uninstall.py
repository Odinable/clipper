"""Removes Clipper: shortcuts, the Windows "installed apps" entry and the program folder.

Your reels (Videos\\Clipper, or the folder picked in the app) are kept. Sign-in and window settings (AppData\\Local\\Clipper) are removed.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Clipper"


def _shortcuts():
    appdata = Path(os.environ.get("APPDATA", ""))
    yield appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Clipper.lnk"
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
        yield Path(buf.value) / "Clipper.lnk"
    except Exception:
        yield Path.home() / "Desktop" / "Clipper.lnk"


def run(silent=None):
    silent = ("/S" in sys.argv or "--silent" in sys.argv) if silent is None else silent
    install_dir = Path(sys.executable).parent
    if not silent:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        ok = messagebox.askyesno(
            "Uninstall Clipper",
            "Remove Clipper from this PC?\n\nYour reels are kept.",
            icon="question")
        root.destroy()
        if not ok:
            return

    # Close a running Clipper first
    subprocess.run(["taskkill", "/IM", "Clipper.exe", "/F", "/FI", f"PID ne {os.getpid()}"],
                   capture_output=True, creationflags=0x08000000)

    for lnk in _shortcuts():
        try:
            lnk.unlink()
        except OSError:
            pass
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    shutil.rmtree(Path(os.environ.get("LOCALAPPDATA", "")) / "Clipper", ignore_errors=True)

    # The running exe can't delete its own folder, so a hidden cmd does it once we've exited.
    subprocess.Popen(
        f'cmd /c ping 127.0.0.1 -n 3 > nul & rmdir /s /q "{install_dir}"',
        creationflags=0x08000000, close_fds=True)

    if not silent:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Clipper", "Clipper has been removed. Your reels are still in their folder.")
        root.destroy()
