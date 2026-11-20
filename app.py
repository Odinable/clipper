"""Clipper app: the live + VOD clipper with a proper window.

Installed: open Clipper from the Start menu. From source: pythonw app.py. It runs a small server on this PC only
(127.0.0.1) and opens it as its own app window. Close the window and it quits
(unless a live session or a VOD job is still running; those finish first).
"""
import ctypes, json, os, re, shutil, subprocess, sys, threading, time
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


# ------------------------------------------------------------------ log capture
class Tee:
    """Keeps the last lines printed by the engines so the app can show them."""
    def __init__(self, real):
        self.real, self.lines, self.cur = real, deque(maxlen=300), ""

    def write(self, s):
        if self.real:
            try:
                self.real.write(s)
            except Exception:
                pass
        for ch in s.replace("\r\n", "\n"):
            if ch == "\n":
                if self.cur.strip():
                    self.lines.append(self.cur.rstrip())
                self.cur = ""
            elif ch == "\r":
                self.cur = ""
            else:
                self.cur += ch

    def flush(self):
        pass

    def isatty(self):
        return False


LOG = Tee(sys.stdout)
sys.stdout = LOG
sys.stderr = Tee(sys.stderr)
sys.stdin = None  # no console keyboard thread inside the app

import clip as C   # noqa: E402
import live as L   # noqa: E402

OUT = C.REELS
RES = C.RES
os.chdir(C.DATA)
OUT.mkdir(exist_ok=True)
PORT = int(os.environ.get("CLIPPER_PORT", "4848"))

S = {"live": None, "live_thread": None, "error": None, "rev": 0, "last_ping": time.time(),
     "vod": {"running": False, "stage": "", "frac": 0.0, "session": None, "input": ""},
     "busy": set(), "hotkey": False}
LOCK = threading.Lock()


# ------------------------------------------------------------------ sign-in (Growthforge account)
# The sign-up form lives on growthforge.cloud. After the email is verified, the site sends back an
# access pass signed with Growthforge's private key. This app only holds the PUBLIC key, so it can
# check a pass is genuine but can't make one. Nothing secret lives in this (open-source) code.
PASS_PUBLIC_KEY = "YhHGwx7T/zxEW/MHAaDpszXeekJokriktgtuQ0QpGKk="
SIGNUP_URL = os.environ.get("CLIPPER_SIGNUP_URL", "https://growthforge.cloud/clipper/start")
ACCOUNT = C.DATA / ".account.json"
SIGNIN_STATE = os.urandom(16).hex()   # ties a returning pass to a sign-in this app started


def check_pass(token):
    """Return the pass's details if Growthforge signed it, else None."""
    import base64
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        body, sig = token.split(".", 1)
        pad = lambda s: s + "=" * (-len(s) % 4)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(PASS_PUBLIC_KEY)).verify(
            base64.urlsafe_b64decode(pad(sig)), body.encode())
        data = json.loads(base64.urlsafe_b64decode(pad(body)))
        return data if data.get("v") == 1 and "@" in data.get("sub", "") else None
    except Exception:
        return None


def account():
    try:
        tok = json.loads(ACCOUNT.read_text(encoding="utf-8"))["pass"]
        return check_pass(tok)
    except Exception:
        return None


def signed_in():
    return account() is not None


def bump():
    S["rev"] += 1


def live_running():
    t = S["live_thread"]
    return bool(t and t.is_alive())


# ------------------------------------------------------------------ actions
CAM_PRESETS = {  # 480x270 webcam, 46px in from the edges of a 1920x1080 stream
    "br": "1394,764,480,270", "bl": "46,764,480,270", "tr": "1394,46,480,270", "tl": "46,46,480,270"}


def apply_look(a, p):
    a.layout = p.get("layout", "fit") if p.get("layout") in ("fit", "cam", "crop", "none") else "fit"
    a.caption = str(p.get("caption", ""))[:120]
    a.chip = str(p.get("chip", ""))[:40]
    a.cam = CAM_PRESETS.get(p.get("cam"), CAM_PRESETS["br"])
    a.no_wide = not p.get("wide", True)


def start_live(p):
    if live_running():
        return {"ok": False, "error": "Already watching a stream."}
    src = (p.get("url") or "").strip()
    if not src:
        return {"ok": False, "error": "Paste your live stream link first."}
    args = [src]
    if p.get("simulate"):
        args.append("--simulate")
    a = L.build_parser().parse_args(args)
    apply_look(a, p)
    a.len = float(p.get("len", 30))
    a.sensitivity = float(p.get("sensitivity", 6))
    if p.get("warmup") is not None:
        a.warmup = float(p["warmup"])
    if a.simulate and not a.chat:
        f = C.find_chat_file(Path(src))
        a.chat = str(f) if f else None
    lv = L.Live(a)

    def run():
        try:
            lv.run()
        except Exception as e:  # keep the app alive, show the problem
            S["error"] = f"Live stopped: {e}"
            print("ERROR", e)
        bump()

    S["live"], S["error"] = lv, None
    S["live_thread"] = threading.Thread(target=run, daemon=True)
    S["live_thread"].start()
    bump()
    return {"ok": True, "session": lv.outdir.name}


def start_vod(p):
    if S["vod"]["running"]:
        return {"ok": False, "error": "Already working on a video."}
    src = (p.get("input") or "").strip().strip('"')
    if not src:
        return {"ok": False, "error": "Paste a link or choose a video file first."}
    a = C.build_parser().parse_args([src])
    a.top = int(p.get("top", 10))
    a.len = float(p.get("len", 30))
    apply_look(a, p)
    v = S["vod"]
    v.update(running=True, stage="starting", frac=0.0, session=None, input=src)
    S["error"] = None

    def hook(stage, frac):
        v["stage"], v["frac"] = stage, frac

    def run():
        C.progress_hook = hook
        try:
            if src.startswith("http"):
                v["stage"] = "downloading"
            out = C.run_vod(a)
            v["session"] = Path(out).name
            v["stage"], v["frac"] = "done", 1.0
        except (RuntimeError, SystemExit) as e:
            S["error"] = str(e)
            v["stage"] = "failed"
        except Exception as e:
            S["error"] = f"Something went wrong: {e}"
            v["stage"] = "failed"
        finally:
            v["running"] = False
            C.progress_hook = None
            bump()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def session_dir(name):
    d = (OUT / name).resolve()
    if OUT.resolve() not in d.parents:
        raise ValueError("bad session")
    return d


def live_for(name):
    lv = S["live"]
    if lv and lv.outdir.name == name and live_running():
        return lv
    return None


def load_job(name):
    lv = live_for(name)
    if lv:
        return lv.job
    return json.loads((session_dir(name) / "highlights.json").read_text(encoding="utf-8"))


def save_job(name, job):
    lv = live_for(name)
    if lv:
        lv.save()
    else:
        d = session_dir(name)
        (d / "highlights.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
        C.write_review(job, d)
        C.write_timestamps(job, d)
    bump()


def recut(p):
    name, cid = p["session"], int(p["id"])
    job = load_job(name)
    c = next((c for c in job["clips"] if c["id"] == cid), None)
    if not c:
        return {"ok": False, "error": "Clip not found."}
    if "caption" in p:
        c["caption"] = p["caption"].strip()
    for k in ("start", "end"):
        if p.get(k) is not None:
            c[k] = round(float(p[k]), 1)
    key = (name, cid)
    S["busy"].add(key)
    bump()

    def run():
        try:
            C.render_one(job, c, session_dir(name))
            save_job(name, job)
        finally:
            S["busy"].discard(key)
            bump()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def delete_clip(p):
    name, cid = p["session"], int(p["id"])
    job = load_job(name)
    for c in job["clips"]:
        if c["id"] == cid:
            c["keep"] = False
    save_job(name, job)
    return {"ok": True}


def sessions():
    out = []
    for d in OUT.iterdir():
        j = d / "highlights.json"
        if d.is_dir() and j.exists():
            try:
                job = json.loads(j.read_text(encoding="utf-8"))
            except Exception:
                continue
            n = len([c for c in job.get("clips", []) if c.get("keep", True)])
            out.append({"name": d.name, "live": d.name.startswith("live-"), "clips": n,
                        "title": Path(job.get("vod", d.name)).name, "mtime": j.stat().st_mtime})
    out.sort(key=lambda s: -s["mtime"])
    return out


def session_view(name):
    job = load_job(name)
    d = session_dir(name)
    clips = []
    for c in job["clips"]:
        if not c.get("keep", True):
            continue
        cc = {k: c.get(k) for k in ("id", "start", "end", "peak", "score", "why", "caption")}
        files = {}
        for k, f in (c.get("files") or {}).items():
            fp = d / f
            if fp.exists():
                files[k] = {"name": f, "url": f"/out/{name}/{f}?v={int(fp.stat().st_mtime)}"}
        cc["files"] = files
        cc["busy"] = (name, c["id"]) in S["busy"]
        clips.append(cc)
    ts = d / "timestamps.txt"
    return {"name": name, "title": Path(job.get("vod", name)).name, "duration": job.get("duration", 0),
            "curve": job.get("curve", []), "signals": job.get("signals", []), "layout": job.get("layout"),
            "url": job.get("url"), "clips": clips,
            "timestamps": ts.read_text(encoding="utf-8") if ts.exists() else ""}


def state():
    lv = S["live"]
    live = None
    if lv:
        alive = live_running()
        if not alive:
            phase = "stopped"
        elif lv.done.is_set():
            phase = "stopping"
        elif lv.now <= 0:
            phase = "connecting"
        elif lv.now < lv.a.warmup:
            phase = "learning"
        else:
            phase = "watching"
        live = {"phase": phase, "running": alive, "now": lv.now, "warmup": lv.a.warmup,
                "threshold": lv.a.sensitivity, "meter": [round(x, 2) for x in lv.curve[-240:]],
                "session": lv.outdir.name, "clips": len(lv.job["clips"]), "rev": lv.rev,
                "cutting": lv.cutting["id"] if lv.cutting else None,
                "pending": [{"id": c["id"], "start": c["start"], "end": c["end"], "peak": c["peak"],
                             "why": c["why"]} for c in lv.pending_list()],
                "has_chat": lv.has_chat, "source": lv.a.source}
    v = S["vod"]
    return {"live": live, "vod": dict(v), "error": S["error"], "rev": S["rev"] + (lv.rev if lv else 0),
            "log": list(LOG.lines)[-60:], "hotkey": S["hotkey"], "nvenc": C.NVENC,
            "account": (lambda a: {"email": a["sub"], "name": a.get("name", "")} if a else None)(account())}


def sign_out():
    if live_running():
        return {"ok": False, "error": "Stop watching the stream first."}
    try:
        ACCOUNT.unlink()
    except FileNotFoundError:
        pass
    bump()
    return {"ok": True}


def pick_file():
    """Native Windows file picker (the browser can't hand us a full path)."""
    res = {}

    def run():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        res["path"] = filedialog.askopenfilename(
            title="Choose a stream recording",
            filetypes=[("Videos", "*.mp4 *.mkv *.mov *.flv *.ts *.webm"), ("All files", "*.*")])
        root.destroy()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return {"path": (res.get("path") or "").replace("/", "\\")}


OPEN_OK = re.compile(r"^(https://growthforge\.cloud(/[^\s]*)?"
                     r"|https://cal\.com/growthforge-basczx/30min"
                     r"|https://www\.instagram\.com/growthforge\.cloud/?"
                     r"|https://policies\.google\.com/privacy"
                     r"|mailto:growthforge\.cloud@gmail\.com(\?subject=[\w%]+)?)$")


def open_external(url):
    """Open our own links in the default browser / mail app (only these, nothing a page could inject)."""
    if not OPEN_OK.match(url or ""):
        return {"ok": False}
    os.startfile(url)
    return {"ok": True}


def reveal(p):
    d = session_dir(p["session"])
    f = p.get("file")
    if f:
        subprocess.Popen(["explorer", "/select,", str(d / f)])
    else:
        os.startfile(d)
    return {"ok": True}


# ------------------------------------------------------------------ global hotkey (F9)
def hotkey_thread():
    """F9 anywhere on the PC = clip that (even while OBS or a game has focus)."""
    if os.name != "nt":
        return
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x4000, 0x78):  # MOD_NOREPEAT, VK_F9
        print("F9 hotkey is taken by another app; use the CLIP button instead.")
        return
    S["hotkey"] = True
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            lv = S["live"]
            if lv and live_running() and not lv.done.is_set():
                lv.manual("you hit clip")
                bump()


# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                b = (RES / "ui.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path == "/api/state":
                S["last_ping"] = time.time()
                self.send_json(state())
            elif u.path == "/api/sessions":
                self.send_json(sessions())
            elif u.path == "/api/session":
                self.send_json(session_view(q["name"][0]))
            elif u.path == "/signin":
                S["signin_at"] = time.time()
                # Hand off to Growthforge's sign-up page; it sends the pass back to /activated.
                ret = f"http://127.0.0.1:{PORT}/activated"
                self.send_response(302)
                self.send_header("Location", f"{SIGNUP_URL}?return={quote(ret, safe='')}&state={SIGNIN_STATE}")
                self.end_headers()
            elif u.path == "/activated":
                tok = (q.get("pass") or [""])[0]
                ok = (q.get("state") or [""])[0] == SIGNIN_STATE and check_pass(tok)
                if ok:
                    ACCOUNT.write_text(json.dumps({"pass": tok}), encoding="utf-8")
                    bump()
                    self.send_response(302)
                    self.send_header("Location", "/?welcome=1")
                    self.end_headers()
                else:
                    b = (b"<!doctype html><meta charset=utf-8><title>Clipper</title><body style='background:#0B0B0F;"
                         b"color:#F5F5F7;font:16px Arial;display:grid;place-items:center;height:100vh;margin:0'><div>"
                         b"<p>That sign-in didn't go through. Please try again.</p><p><a style='color:#FF6B2C' "
                         b"href='/signin'>Sign in again</a></p></div>")
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
            elif u.path.startswith("/out/"):
                self.send_file(unquote(u.path[5:]))
            elif u.path.rstrip("/") in ("/tutorial", "/privacy", "/contact"):
                b = (RES / "pages" / (u.path.strip("/") + ".html")).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path.startswith("/brand/"):
                f = (RES / "brand" / Path(unquote(u.path[7:])).name)
                if not f.is_file():
                    return self.send_error(404)
                b = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", {".png": "image/png", ".woff2": "font/woff2", ".ttf": "font/ttf", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(f.suffix, "application/octet-stream"))
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)
        except (FileNotFoundError, KeyError, ValueError) as e:
            self.send_json({"error": str(e)}, 404)
        except (ConnectionError, BrokenPipeError):
            pass

    def send_file(self, rel):
        f = (OUT / rel).resolve()
        if OUT.resolve() not in f.parents or not f.is_file():
            return self.send_error(404)
        size = f.stat().st_size
        ctype = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".txt": "text/plain"}.get(f.suffix, "application/octet-stream")
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if a and b else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(f, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_POST(self):
        u = urlparse(self.path)
        # Only the Clipper window may call the API. Other websites open in your browser could
        # otherwise post to 127.0.0.1; they can't send a matching Origin with a JSON body.
        origin = self.headers.get("Origin", "")
        if origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}") or \
                not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send_json({"ok": False, "error": "Not allowed."}, 403)
        if u.path not in ("/api/open", "/api/dismiss", "/api/live/stop") and not signed_in():
            return self.send_json({"ok": False, "error": "Please sign in first.", "signin": True}, 401)
        try:
            p = self.body()
            r = {
                "/api/signout": sign_out,
                "/api/live/start": lambda: start_live(p),
                "/api/live/stop": lambda: (S["live"].stop() if S["live"] else None, bump(), {"ok": True})[-1],
                "/api/clip": lambda: (S["live"].manual("you hit clip") if live_running() else None, bump(), {"ok": True})[-1],
                "/api/vod": lambda: start_vod(p),
                "/api/recut": lambda: recut(p),
                "/api/delete": lambda: delete_clip(p),
                "/api/pick": pick_file,
                "/api/reveal": lambda: reveal(p),
                "/api/dismiss": lambda: (S.update(error=None), {"ok": True})[-1],
                "/api/open": lambda: open_external(p.get("url", "")),
            }.get(u.path)
            if not r:
                return self.send_error(404)
            self.send_json(r())
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)


def find_browser():
    cands = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in cands if os.path.exists(c)), None)


def open_window():
    url = f"http://127.0.0.1:{PORT}/"
    b = find_browser()
    if b:
        subprocess.Popen([b, f"--app={url}", "--window-size=1360,900",
                          f"--user-data-dir={C.DATA / '.appwindow'}", "--no-first-run",
                          "--autoplay-policy=no-user-gesture-required"])
    else:
        import webbrowser
        webbrowser.open(url)


def watchdog():
    """Quit when the window has been closed for a while and nothing is running."""
    while True:
        time.sleep(5)
        idle = time.time() - S["last_ping"] > 40
        if time.time() - S.get("signin_at", 0) < 900:   # waiting on the sign-up page / email code
            idle = False
        if idle and not live_running() and not S["vod"]["running"] and not S["busy"]:
            os._exit(0)


def main():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        open_window()          # already running: just show it
        return
    threading.Thread(target=hotkey_thread, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    if "--no-window" not in sys.argv:
        open_window()
    print(f"Clipper running at http://127.0.0.1:{PORT}/")
    srv.serve_forever()


if __name__ == "__main__":
    main()
