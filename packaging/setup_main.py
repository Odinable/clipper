"""Clipper-Setup.exe: installs Clipper for the current Windows user (no admin needed).

  Clipper-Setup.exe        branded install window
  Clipper-Setup.exe /S     silent install (no window), e.g. for testing
  Clipper-Setup.exe /S /D=E:\\Apps   silent install into E:\\Apps\\Clipper

Installs to %LOCALAPPDATA%\\Programs\\Clipper by default, or the folder picked in the window (an update
keeps the folder used last time). Adds Start-menu (and optionally desktop) shortcuts and registers in
Settings > Apps so it can be uninstalled normally.
"""
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

VERSION = "1.0.4"
APP = "Clipper"
PUBLISHER = "Growthforge"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / APP
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Clipper"
NOWIN = 0x08000000
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
PAYLOAD = RES / "payload.zip"

BG, CARD, LINE, TEXT, MUT, ACCENT, EMBER = "#0B0B0F", "#16161C", "#26262F", "#F5F5F7", "#A1A1AA", "#FF6B2C", "#FFA23A"


def desktop_dir():
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)
        return Path(buf.value)
    except Exception:
        return Path.home() / "Desktop"


def make_shortcut(lnk, target, workdir, icon):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath=$env:TGT;$s.WorkingDirectory=$env:WD;$s.IconLocation=$env:ICO;"
        "$s.Description='Clipper by Growthforge';$s.Save()"
    )
    env = dict(os.environ, LNK=str(lnk), TGT=str(target), WD=str(workdir), ICO=f"{icon},0")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   env=env, capture_output=True, creationflags=NOWIN, check=True)


def previous_dir():
    """Where Clipper is installed now, if it is."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            d = Path(winreg.QueryValueEx(k, "InstallLocation")[0])
        return d if (d / "Clipper.exe").exists() else None
    except OSError:
        return None


def target_dir(chosen):
    """Always a folder of Clipper's own: picking D:\\Games installs to D:\\Games\\Clipper, never into D:\\Games
    itself (an update empties the install folder first, so it must never hold anything else)."""
    p = Path(chosen).expanduser()
    return p if p.name.lower() == APP.lower() else p / APP


def check_target(d):
    """Raise a plain-English error if Clipper can't (or shouldn't) go in folder d."""
    import shutil
    if not d.is_absolute() or len(d.parts) < 2:
        raise RuntimeError("Pick a folder on one of your drives.")
    if not Path(d.anchor).exists():
        raise RuntimeError(f"Drive {d.anchor} isn't available.")
    if d.exists() and any(d.iterdir()) and not (d / "Clipper.exe").exists():
        raise RuntimeError(f"{d} already has other files in it. Pick a different folder.")
    if shutil.disk_usage(d.anchor).free < 400 * 1024 * 1024:
        raise RuntimeError(f"Drive {d.anchor} has less than 400 MB free. Pick another drive.")
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".clipper-write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        raise RuntimeError(f"Clipper can't write to {d}. Folders like Program Files need admin, so pick another one.")


def register(install_dir, size_kb):
    import winreg
    exe = install_dir / "Clipper.exe"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in {
            "DisplayName": APP, "DisplayVersion": VERSION, "Publisher": PUBLISHER,
            "DisplayIcon": f"{exe},0", "InstallLocation": str(install_dir),
            "UninstallString": f'"{exe}" --uninstall', "QuietUninstallString": f'"{exe}" --uninstall /S',
            "URLInfoAbout": "https://growthforge.cloud/clipper", "HelpLink": "https://growthforge.cloud/clipper",
        }.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)


def close_running():
    subprocess.run(["taskkill", "/IM", "Clipper.exe", "/F"], capture_output=True, creationflags=NOWIN)
    subprocess.run(["taskkill", "/IM", "clipper-yt.exe", "/F"], capture_output=True, creationflags=NOWIN)


def install(install_dir=None, desktop=True, progress=lambda f, msg: None):
    import shutil
    old = previous_dir()
    INSTALL_DIR = Path(install_dir) if install_dir else (old or DEFAULT_DIR)
    progress(0.02, "Checking the folder…")
    check_target(INSTALL_DIR)
    progress(0.03, "Closing Clipper if it's open…")
    close_running()
    progress(0.05, "Removing the old version…")
    if (INSTALL_DIR / "Clipper.exe").exists():   # only ever empties a folder that holds Clipper
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(PAYLOAD) as z:
        items = z.infolist()
        total = sum(i.file_size for i in items) or 1
        done = 0
        for i, it in enumerate(items):
            z.extract(it, INSTALL_DIR)
            done += it.file_size
            if i % 25 == 0:
                progress(0.05 + 0.85 * done / total, "Copying files…")

    progress(0.92, "Adding shortcuts…")
    exe = INSTALL_DIR / "Clipper.exe"
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Clipper.lnk"
    make_shortcut(start_menu, exe, INSTALL_DIR, exe)
    if desktop:
        make_shortcut(desktop_dir() / "Clipper.lnk", exe, INSTALL_DIR, exe)
    register(INSTALL_DIR, total // 1024)
    if old and old.resolve() != INSTALL_DIR.resolve() and (old / "Clipper.exe").exists():
        progress(0.97, "Removing the copy in the old folder…")   # moved: don't leave two copies
        shutil.rmtree(old, ignore_errors=True)
    progress(1.0, "Done")
    return exe


# ------------------------------------------------------------------ window
def gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Install Clipper")
    root.configure(bg=BG)
    root.resizable(False, False)
    W, H = 560, 510
    root.geometry(f"{W}x{H}+{(root.winfo_screenwidth() - W) // 2}+{(root.winfo_screenheight() - H) // 3}")
    try:
        root.iconbitmap(str(RES / "clipper.ico"))
    except Exception:
        pass

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("C.Horizontal.TProgressbar", troughcolor=LINE, background=ACCENT, bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)

    wrap = tk.Frame(root, bg=BG, padx=36, pady=30)
    wrap.pack(fill="both", expand=True)
    try:
        mark = tk.PhotoImage(file=str(RES / "setup-mark.png")).subsample(2, 2)
        tk.Label(wrap, image=mark, bg=BG).pack(anchor="w")
        root._mark = mark
    except Exception:
        pass
    tk.Label(wrap, text="CLIPPER  ·  BY GROWTHFORGE", bg=BG, fg=MUT, font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(16, 4))
    title = tk.Label(wrap, text="Install Clipper", bg=BG, fg=TEXT, font=("Segoe UI", 24, "bold"))
    title.pack(anchor="w")
    body = tk.Label(wrap, text="Reels while you stream. Free forever.\nInstalls just for you, no admin needed.",
                    bg=BG, fg=MUT, font=("Segoe UI", 10), justify="left")
    body.pack(anchor="w", pady=(6, 16))

    desk = tk.BooleanVar(value=True)
    chk = tk.Checkbutton(wrap, text="  Add a desktop shortcut", variable=desk, bg=BG, fg=TEXT, selectcolor=CARD,
                         activebackground=BG, activeforeground=TEXT, font=("Segoe UI", 10), bd=0, highlightthickness=0)
    chk.pack(anchor="w")

    folder = tk.StringVar(value=str(previous_dir() or DEFAULT_DIR))
    frow = tk.Frame(wrap, bg=BG)
    frow.pack(anchor="w", fill="x", pady=(14, 0))
    tk.Label(frow, text="INSTALL FOLDER", bg=BG, fg=MUT, font=("Segoe UI Semibold", 8)).pack(anchor="w")
    fline = tk.Frame(frow, bg=CARD, highlightbackground=LINE, highlightthickness=1)
    fline.pack(fill="x", pady=(4, 0))
    change = tk.Label(fline, text="Change…", bg=LINE, fg=TEXT, font=("Segoe UI Semibold", 9), padx=12, pady=7, cursor="hand2")
    change.pack(side="right")
    tk.Label(fline, textvariable=folder, bg=CARD, fg=TEXT, font=("Segoe UI", 9), anchor="w", padx=10, pady=7
             ).pack(side="left", fill="x", expand=True)

    status = tk.Label(wrap, text="", bg=BG, fg=MUT, font=("Segoe UI", 9))
    bar = ttk.Progressbar(wrap, style="C.Horizontal.TProgressbar", mode="determinate", maximum=1.0)

    btn = tk.Label(wrap, text="Install", bg=ACCENT, fg=BG, font=("Segoe UI Semibold", 11), padx=10, pady=10, cursor="hand2")
    btn.pack(side="bottom", fill="x")
    btn.bind("<Enter>", lambda e: btn.configure(bg=EMBER))
    btn.bind("<Leave>", lambda e: btn.configure(bg=ACCENT))
    tk.Label(wrap, text=f"Version {VERSION}  ·  Clipper gets its own folder inside the one you pick", bg=BG,
             fg="#5c5c66", font=("Segoe UI", 8)).pack(side="bottom", anchor="w", pady=(0, 10))

    state = {"exe": None, "busy": False}

    def pick_folder(_=None):
        if state["busy"] or state["exe"]:
            return
        from tkinter import filedialog
        cur = Path(folder.get())
        start = cur.parent if cur.name.lower() == APP.lower() else cur
        got = filedialog.askdirectory(parent=root, title="Choose where to install Clipper",
                                      initialdir=str(start if start.exists() else Path.home()))
        if got:
            folder.set(str(target_dir(got)))
            status.configure(text="")

    change.bind("<Button-1>", pick_folder)

    def set_progress(f, msg):
        root.after(0, lambda: (bar.configure(value=f), status.configure(text=msg)))

    def finished(exe, err):
        state["busy"] = False
        bar.pack_forget()
        if err:
            status.configure(text=f"Install failed: {err}", fg=ACCENT, wraplength=480, justify="left")
            status.pack(anchor="w")
            btn.configure(text="Try again")
            chk.configure(state="normal")
            return
        state["exe"] = exe
        title.configure(text="Clipper is installed")
        body.configure(text="Find it in your Start menu" + (" and on your desktop." if desk.get() else ".")
                       + "\nThe first time it opens, sign in with your name and email.")
        chk.pack_forget()
        frow.pack_forget()
        status.configure(text="")
        btn.configure(text="Open Clipper")

    def on_click(_=None):
        if state["busy"]:
            return
        if state["exe"]:
            subprocess.Popen([str(state["exe"])], cwd=str(Path(state["exe"]).parent))
            root.destroy()
            return
        state["busy"] = True
        btn.configure(text="Installing…")
        chk.configure(state="disabled")
        status.configure(fg=MUT)
        bar.pack(fill="x", pady=(18, 6))
        status.pack(anchor="w")

        def work():
            try:
                exe = install(folder.get(), desk.get(), set_progress)
                root.after(0, lambda: finished(exe, None))
            except Exception as e:
                root.after(0, lambda: finished(None, e))

        threading.Thread(target=work, daemon=True).start()

    btn.bind("<Button-1>", on_click)
    root.bind("<Return>", on_click)
    root.mainloop()


if __name__ == "__main__":
    if "/S" in sys.argv or "--silent" in sys.argv:
        try:
            d = next((a[3:] for a in sys.argv if a.upper().startswith("/D=")), None)
            install(target_dir(d) if d else None, desktop="--no-desktop" not in sys.argv)
            sys.exit(0)
        except Exception as e:
            sys.stderr.write(str(e))
            sys.exit(1)
    gui()
