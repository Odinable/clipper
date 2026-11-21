"""Clipper by Growthforge: the after-the-stream (VOD) engine.

Finds the highlights in a stream VOD (loud moments + chat spikes), cuts them,
and makes a 16:9 clip and a 9:16 Short of each, plus a review page.

  python clip.py VOD.mp4                     find + cut the top 10 moments
  python clip.py https://youtu.be/XXXX       download your VOD (+ chat replay) first
  python clip.py VOD.mp4 --at 1:02:30 2:14:05   also clip these times ("clip that" moments)
  python clip.py VOD.mp4 --layout cam        Short = webcam on top, gameplay below
  python clip.py --render out/VOD/highlights.json   re-cut after editing the JSON

See README.md for every option.
"""
import argparse, json, math, os, re, shutil, subprocess, sys, html
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

APP_VERSION = "1.0.3"
HERE = Path(__file__).resolve().parent
NOWIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: no console flashes from the app

# Installed (frozen) vs. run from source:
#   RES   read-only files shipped with the app (fonts, pages, brand, ui.html, bin/ffmpeg.exe)
#   DATA  per-user app data: sign-in, app window profile, downloaded VODs
#   REELS where reels are saved; the user's Videos\Clipper when installed
FROZEN = getattr(sys, "frozen", False)
RES = Path(getattr(sys, "_MEIPASS", HERE))


def _videos_dir():
    """The real Videos folder (it may be moved, e.g. into OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes
        guid = (ctypes.c_byte * 16).from_buffer_copy(
            bytes.fromhex("1d9b9818b599 5b45 841cab7c74e4ddfc".replace(" ", "")))  # FOLDERID_Videos
        ptr = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) == 0:
            p = Path(ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            return p
    except Exception:
        pass
    return Path.home() / "Videos"


if FROZEN:
    DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Clipper"
    REELS = Path(os.environ.get("CLIPPER_REELS") or (_videos_dir() / "Clipper"))
else:
    DATA = HERE
    REELS = Path(os.environ.get("CLIPPER_REELS") or (HERE / "out"))
VODS = DATA / "vods"
for _d in (DATA, REELS):
    _d.mkdir(parents=True, exist_ok=True)

FONT_BOLD = RES / "fonts" / "Inter-700.ttf"
FONT_MED = RES / "fonts" / "Inter-500.ttf"
FONT_DISPLAY = RES / "fonts" / "SpaceGrotesk-var.woff2"   # Growthforge display face (variable 300-700)


def ytdlp_cmd():
    """How to run yt-dlp: the bundled helper exe when installed, the Python module from source."""
    if FROZEN:
        return [str(Path(sys.executable).with_name("clipper-yt.exe"))]
    return [sys.executable, "-m", "yt_dlp"]

# Growthforge palette
FORGE_BLACK, ANVIL, SEAM, WHITE_HEAT, ASH, MOLTEN, EMBER = "#0B0B0F", "#16161C", "#26262F", "#F5F5F7", "#A1A1AA", "#FF6B2C", "#FFA23A"
INK = FORGE_BLACK


def display_font(size, weight=700):
    """Space Grotesk at a given weight (falls back to Inter Bold)."""
    try:
        f = ImageFont.truetype(str(FONT_DISPLAY), size)
        f.set_variation_by_axes([weight])
        return f
    except Exception:
        return ImageFont.truetype(str(FONT_BOLD), size)

HYPE = re.compile(r"(\bw+\b|\bl+\b|lmao+|lol+|\blmf?ao|😂|🤣|💀|🔥|omg+|wtf|bro+\b|no way|clip|ayo+|holy|insane|crazy|goat|\?\?\?|!!!)", re.I)


def ffmpeg_exe():
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    bundled = RES / "bin" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FF = ffmpeg_exe()

# the app sets this to get progress: progress_hook(stage, fraction)
progress_hook = None


def progress(stage, frac):
    if progress_hook:
        try:
            progress_hook(stage, frac)
        except Exception:
            pass


def hms(t, ms=False):
    t = max(0, t)
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    out = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return out


def parse_time(s):
    parts = [float(p) for p in str(s).split(":")]
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


# ---------------------------------------------------------------- input

def _ytdlp(args):
    """Run yt-dlp; returns (ok, last printed line, error text)."""
    r = subprocess.run([*ytdlp_cmd(), "--encoding", "utf-8", "--ffmpeg-location", FF, "--no-progress", *args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                       errors="replace", creationflags=NOWIN)
    lines = r.stdout.strip().splitlines()
    errs = [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
    return r.returncode == 0, (lines[-1] if lines else ""), (errs[-1] if errs else r.stderr.strip()[-300:])


def _download_error(err):
    if "No space left" in err or "Errno 28" in err:
        return "Your drive is full. Free up some space and try again."
    if "Private video" in err or "members-only" in err or "Sign in" in err:
        return "That video is private or members-only, so it can't be downloaded."
    if "is not a valid URL" in err or "Unsupported URL" in err:
        return "That link isn't a YouTube or Twitch video."
    return "Couldn't download that link. " + err.replace("ERROR: ", "")[:200]


def fetch_audio(url, workdir):
    """Download only the sound (plus the live chat replay, if any). A 6-hour stream is ~130 MB this way,
    against ~10 GB for the full video; the video is then downloaded just around each highlight."""
    workdir.mkdir(parents=True, exist_ok=True)
    tmpl = str(workdir / "%(title).80s [%(id)s].%(ext)s")
    base = ["-f", "139/249/ba[abr<=96]/wa/ba/b", "-o", tmpl, "--print", "after_move:filepath", url]
    print("Downloading the stream's audio and chat...")
    progress("downloading", 0.0)
    ok, path, err = _ytdlp(["--write-subs", "--sub-langs", "live_chat", *base])
    if not ok:  # the chat replay sometimes fails; retry without it
        ok, path, err = _ytdlp(base)
    if not ok or not path:
        raise RuntimeError(_download_error(err))
    return Path(path)


SECTION_PAD = 20  # seconds downloaded either side of a clip, so it can be nudged later without downloading again


def fetch_section(url, start, end, out):
    """Download one stretch of the video at up to 1080p."""
    out.parent.mkdir(parents=True, exist_ok=True)
    for f in out.parent.glob(out.stem + ".*"):
        f.unlink()
    ok, path, err = _ytdlp(["-f", "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
                            "--merge-output-format", "mp4", "--download-sections", f"*{start:.1f}-{end:.1f}",
                            "-o", str(out.with_suffix(".%(ext)s")), "--print", "after_move:filepath", url])
    if not ok or not path:
        raise RuntimeError(_download_error(err))
    return Path(path)


def ensure_source(job, c, outdir):
    """For a clip from a downloaded link, make sure its stretch of video is on disk and covers start..end."""
    if not job.get("sections"):
        return
    have = c.get("src_range")
    src = outdir / c["src"] if c.get("src") else None
    if have and src and src.exists() and have[0] <= c["start"] and c["end"] <= have[1]:
        return
    s = max(0.0, c["start"] - SECTION_PAD)
    e = min(job["duration"], c["end"] + SECTION_PAD)
    f = fetch_section(job["url"], s, e, outdir / "source" / f"{c['id']:02d}")
    c["src"], c["src_offset"], c["src_range"] = str(f.relative_to(outdir)), s, [s, e]
    if not job.get("size_checked"):  # the audio-only download can't tell us the video size
        _, job["width"], job["height"], _ = probe(f)
        job["size_checked"] = True


def probe(path):
    """Duration, width, height via ffmpeg's stderr (no ffprobe in imageio-ffmpeg)."""
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)], stderr=subprocess.PIPE, text=True, errors="ignore", creationflags=NOWIN)
    err = r.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise RuntimeError(f"Couldn't read {path}. Is it a video file, and is the stream live?")
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", err)
    w, h = (int(v[1]), int(v[2])) if v else (1920, 1080)
    has_audio = "Audio:" in err
    return dur, w, h, has_audio


# ---------------------------------------------------------------- analysis

HOP = 0.5  # seconds per analysis step


def audio_loudness(path, dur):
    """Loudness (dB) every HOP seconds, streamed so a 6h VOD doesn't fill RAM."""
    sr = 8000
    step = int(sr * HOP)
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
           "-ar", str(sr), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, creationflags=NOWIN)
    out, buf = [], b""
    total = int(dur / HOP) + 1
    last_pct = -1
    while True:
        chunk = p.stdout.read(step * 2 * 240)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // (step * 2)
        if n:
            a = np.frombuffer(buf[: n * step * 2], dtype=np.int16).astype(np.float32).reshape(n, step)
            buf = buf[n * step * 2:]
            rms = np.sqrt((a ** 2).mean(axis=1)) + 1.0
            out.extend(20 * np.log10(rms / 32768.0))
            pct = min(99, int(100 * len(out) / total))
            progress("listening", len(out) / total)
            if pct // 5 != last_pct // 5:
                print(f"\r  listening to audio... {pct}%", end="", flush=True)
                last_pct = pct
    p.wait()
    print("\r  listening to audio... done   ")
    return np.array(out, dtype=np.float32)


def rolling(x, win, fn):
    win = max(1, int(win))
    if len(x) < win:
        return np.full_like(x, fn(x) if len(x) else 0)
    pad = win // 2
    xp = np.pad(x, (pad, win - 1 - pad), mode="edge")
    v = np.lib.stride_tricks.sliding_window_view(xp, win)
    # median over long windows is slow on big arrays; subsample the window
    if fn is np.median and win > 120:
        v = v[:, :: win // 120]
    return fn(v, axis=1)


def smooth(x, win):
    win = max(1, int(win))
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def robust_z(x, baseline_win, floor):
    """How far above its local baseline x is, in robust standard deviations.
    `floor` stops a near-silent or near-constant signal turning tiny wiggles into huge scores."""
    base = rolling(x, baseline_win, np.median)
    d = x - base
    mad = max(np.median(np.abs(d - np.median(d))) * 1.4826, floor)
    return d / mad


def squash(x):
    """Soft cap at ~12 that keeps the ordering (a hard clip makes every big moment tie)."""
    return np.where(x > 0, 12 * np.tanh(x / 12), np.maximum(x, -3))


def find_chat_file(vod):
    for c in vod.parent.glob(vod.stem + "*.live_chat.json"):
        return c
    c = vod.with_suffix(".live_chat.json")
    return c if c.exists() else None


def load_chat(path):
    """YouTube live-chat replay (yt-dlp .live_chat.json, one JSON object per line).
    Also accepts a simple CSV/TXT of `seconds,message` or `h:mm:ss message` lines (e.g. Twitch exports)."""
    msgs = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                r = o.get("replayChatItemAction", {})
                t = r.get("videoOffsetTimeMsec")
                if t is None:
                    continue
                for a in r.get("actions", []):
                    item = a.get("addChatItemAction", {}).get("item", {})
                    rend = (item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                            or item.get("liveChatMembershipItemRenderer"))
                    if not rend:
                        continue
                    runs = rend.get("message", {}).get("runs", [])
                    text = "".join(x.get("text") or x.get("emoji", {}).get("shortcuts", [""])[0] for x in runs)
                    paid = "liveChatPaidMessageRenderer" in item
                    msgs.append((int(t) / 1000.0, text, paid))
            else:
                m = re.match(r"\[?([\d:.]+)\]?[,\s]+(.*)", line)
                if m:
                    try:
                        msgs.append((parse_time(m[1]), m[2], False))
                    except ValueError:
                        pass
    return msgs


def chat_signal(msgs, n, lag):
    """Messages per HOP, weighted up for hype words and Super Chats, shifted back by `lag`
    seconds because chat reacts after the moment happens."""
    rate = np.zeros(n, dtype=np.float32)
    hype = np.zeros(n, dtype=np.float32)
    shift = int(lag / HOP)
    for t, text, paid in msgs:
        i = int(t / HOP) - shift
        if 0 <= i < n:
            rate[i] += 1
            if HYPE.search(text or ""):
                hype[i] += 1
            if paid:
                hype[i] += 3
    return rate, hype


def analyse(vod, dur, has_audio, chat_path, lag):
    n = int(dur / HOP) + 1
    parts = {}
    if has_audio:
        db = audio_loudness(vod, dur)[:n]
        db = np.pad(db, (0, n - len(db)), constant_values=db.min() if len(db) else -90)
        # short bursts (shouting / explosions) vs a 2-minute baseline
        burst = robust_z(smooth(db, 4), 240, 2.0)     # ~2 s window vs a 2-minute baseline (dB)
        sustain = robust_z(smooth(db, 16), 240, 2.0)  # ~8 s of keeps-going hype
        parts["audio"] = squash(0.6 * burst + 0.4 * sustain)
        parts["db"] = db
    msgs = load_chat(chat_path) if chat_path else []
    if msgs:
        rate, hype = chat_signal(msgs, n, lag)
        r = robust_z(smooth(rate, 20), 600, 0.15)     # 10 s window vs 5 min baseline (msgs per 0.5 s)
        h = robust_z(smooth(hype, 20), 600, 0.1)
        parts["chat"] = squash(0.5 * r + 0.8 * h)
        print(f"  read {len(msgs):,} chat messages")
    score = np.zeros(n, dtype=np.float32)
    if "audio" in parts:
        score += parts["audio"]
    if "chat" in parts:
        score += 1.3 * parts["chat"]
    return score, parts, len(msgs)


def pick_peaks(score, parts, top, length, pre_ratio, dur, skip_start):
    s = score.copy()
    s[: int(skip_start / HOP)] = -1e9                 # skip "starting soon" screen
    out = []
    gap = int(length / HOP)
    for _ in range(top):
        i = int(np.argmax(s))
        if s[i] < 3:                                  # nothing stands out any more
            break
        t = i * HOP
        why = []
        if "audio" in parts and parts["audio"][i] > 2:
            why.append("loud moment")
        if "chat" in parts and parts["chat"][i] > 2:
            why.append("chat went off")
        start = max(0.0, t - length * pre_ratio)
        end = min(dur, start + length)
        out.append({"peak": round(t, 1), "start": round(start, 1), "end": round(end, 1),
                    "score": round(float(score[i]), 1), "why": why or ["spike"]})
        s[max(0, i - gap): i + gap] = -1e9
    return out


# ---------------------------------------------------------------- rendering

def caption_png(text, out, width=1080, chip=""):
    """Brand caption badge as a transparent PNG (Pillow handles fonts; no ffmpeg escaping)."""
    font = display_font(68)
    small = display_font(24, 500)
    words, lines, cur = text.upper().split(), [], ""
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=font) > width - 160 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    lines = lines[:3]
    lh = 76
    top = 70 if chip else 0
    h = top + lh * len(lines) + 40
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # chip
    if chip:
        chip = chip.upper()[:40]
        cw = d.textlength(chip, font=small) + 48
        d.rounded_rectangle(((width - cw) / 2, 0, (width + cw) / 2, 50), 25, fill=MOLTEN)
        d.text((width / 2, 26), chip, font=small, fill=FORGE_BLACK, anchor="mm")
    # shadowed headline
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for k, ln in enumerate(lines):
        y = top + k * lh + lh / 2
        sd.text((width / 2, y + 4), ln, font=font, fill=(0, 0, 0, 200), anchor="mm")
    img = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)), img)
    d = ImageDraw.Draw(img)
    for k, ln in enumerate(lines):
        y = top + k * lh + lh / 2
        d.text((width / 2, y), ln, font=font, fill=WHITE_HEAT, anchor="mm", stroke_width=3, stroke_fill=FORGE_BLACK)
    img.save(out)
    return h


def encoder_args():
    return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"] if NVENC else \
           ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _nvenc_ok():
    r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, creationflags=NOWIN)
    return r.returncode == 0


NVENC = _nvenc_ok()


def run_ff(args, label):
    r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, errors="ignore", creationflags=NOWIN)
    if r.returncode != 0:
        print(f"\n  ffmpeg failed on {label}:\n{r.stderr[-1500:]}")
        return False
    return True


def cut_wide(vod, c, out):
    dur = c["end"] - c["start"]
    vf = "scale=-2:1080,fade=t=in:st=0:d=0.25,fade=t=out:st=%.2f:d=0.4" % max(0, dur - 0.4)
    af = "afade=t=in:st=0:d=0.25,afade=t=out:st=%.2f:d=0.4" % max(0, dur - 0.4)
    return run_ff(["-ss", f"{c['start']:.2f}", "-i", str(vod), "-t", f"{dur:.2f}", "-vf", vf, "-af", af,
                   *encoder_args(), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], out.name)


def cut_vert(vod, c, out, layout, cam, src_w, src_h, cap_png, cap_h):
    """9:16 Short. layouts:
       fit  - full 16:9 gameplay in the middle over a blurred, darkened copy of itself
       cam  - webcam crop on top (1080x608), gameplay centre-crop below (1080x1312)
       crop - gameplay centre-cropped to fill the whole frame"""
    dur = c["end"] - c["start"]
    fade = "fade=t=in:st=0:d=0.25,fade=t=out:st=%.2f:d=0.4" % max(0, dur - 0.4)
    if layout == "cam":
        sx, sy = src_w / 1920, src_h / 1080
        x, y, w, h = cam
        g = f"[0:v]split[a][b];" \
            f"[a]crop={int(w*sx)}:{int(h*sy)}:{int(x*sx)}:{int(y*sy)},scale=1080:608:force_original_aspect_ratio=increase,crop=1080:608[cam];" \
            f"[b]crop=ih*1080/1312:ih:(iw-ow)/2:0,scale=1080:1312[game];" \
            f"[cam][game]vstack,setsar=1[base]"
        cap_y = 608 - 40
    elif layout == "crop":
        g = "[0:v]crop=ih*9/16:ih:(iw-ow)/2:0,scale=1080:1920,setsar=1[base]"
        cap_y = 170
    else:  # fit
        g = "[0:v]split[a][b];" \
            "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,eq=brightness=-0.18:saturation=1.2,scale=1080:1920[bg];" \
            "[b]scale=1080:-2[fg];[bg][fg]overlay=0:(H-h)/2+40,setsar=1[base]"
        cap_y = max(60, (1920 - 608) // 2 + 40 - cap_h - 30)
    inputs = ["-ss", f"{c['start']:.2f}", "-i", str(vod)]
    if cap_png:
        inputs += ["-i", str(cap_png)]
        g += f";[base][1:v]overlay=0:{cap_y}[cap];[cap]{fade}[v]"
    else:
        g += f";[base]{fade}[v]"
    af = "afade=t=in:st=0:d=0.25,afade=t=out:st=%.2f:d=0.4" % max(0, dur - 0.4)
    return run_ff([*inputs, "-t", f"{dur:.2f}", "-filter_complex", g, "-map", "[v]", "-map", "0:a?", "-af", af,
                   *encoder_args(), "-c:a", "aac", "-b:a", "192k", "-r", "60", "-movflags", "+faststart", str(out)], out.name)


def thumb(vod, t, out):
    run_ff(["-ss", f"{t:.2f}", "-i", str(vod), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)], out.name)


def render(job, only=None):
    vod = Path(job["vod"])
    outdir = Path(job["outdir"])
    outdir.mkdir(parents=True, exist_ok=True)
    src_w, src_h = job["width"], job["height"]
    clips = [c for c in job["clips"] if c.get("keep", True)]
    print(f"Cutting {len(clips)} clips ({'NVENC GPU' if NVENC else 'CPU x264'}) -> {outdir}")
    for k, c in enumerate(clips, 1):
        if only and c["id"] not in only:
            continue
        progress("cutting", (k - 1) / len(clips))
        try:
            render_one(job, c, outdir, label=f"[{k}/{len(clips)}]")
        except RuntimeError as e:  # one clip's download failing shouldn't lose the rest
            print(f"  skipped clip {c['id']}: {e}")
            c["keep"] = False
    if clips and not any(c.get("files") for c in clips):
        raise RuntimeError("None of the highlights could be cut. Check your connection and free space, then try again.")
    (outdir / "highlights.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    write_review(job, outdir)
    write_timestamps(job, outdir)
    print(f"\nDone. Open {outdir / 'index.html'} to review.")


def render_one(job, c, outdir, label=""):
    """Cut one clip. Live clips carry their own source file (`src`, starting at stream time
    `src_offset`), so they can be re-cut after the stream's rolling buffer is gone."""
    ensure_source(job, c, outdir)
    src_w, src_h = job["width"], job["height"]
    vod = Path(c["src"]) if c.get("src") else Path(job["vod"])
    if c.get("src") and not vod.is_absolute():
        vod = outdir / vod
    shift = c.get("src_offset", 0)
    base = f"{c['id']:02d}_{hms(c['start']).replace(':', '-')}"
    c["files"] = {}
    print(f"  {label} {hms(c['start'])} - {hms(c['end'])}  {c.get('caption') or ''}", flush=True)
    orig, c = c, dict(c, start=c["start"] - shift, end=c["end"] - shift, peak=c["peak"] - shift)
    c["files"] = orig["files"]
    t = outdir / f"{base}.jpg"
    thumb(vod, c["peak"], t)
    c["files"]["thumb"] = t.name
    if job["wide"]:
        o = outdir / f"{base}_wide.mp4"
        if cut_wide(vod, c, o):
            c["files"]["wide"] = o.name
    if job["layout"] != "none":
        cap_png, cap_h = None, 0
        if c.get("caption") or job.get("chip"):
            cap_png = outdir / f".{base}_cap.png"
            cap_h = caption_png(c.get("caption") or "", cap_png, chip=job.get("chip", ""))
        o = outdir / f"{base}_short.mp4"
        if cut_vert(vod, c, o, job["layout"], job["cam"], src_w, src_h, cap_png, cap_h):
            c["files"]["short"] = o.name
        if cap_png and cap_png.exists():
            cap_png.unlink()
    return orig


# ---------------------------------------------------------------- review page + timestamps

def write_timestamps(job, outdir):
    lines = ["Stream highlights (paste into the VOD description / pinned comment):", ""]
    for c in sorted([c for c in job["clips"] if c.get("keep", True)], key=lambda c: c["start"]):
        lines.append(f"{hms(c['start'])} {c.get('caption') or ', '.join(c['why']).capitalize()}")
    (outdir / "timestamps.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_review(job, outdir):
    curve = job.get("curve", [])
    dur = job["duration"]
    # sparkline of the score over the whole VOD
    W, H = 1000, 120
    pts = ""
    if curve:
        mx = max(max(curve), 1)
        pts = " ".join(f"{i * W / max(1, len(curve) - 1):.1f},{H - max(0, v) / mx * (H - 8):.1f}" for i, v in enumerate(curve))
    marks = ""
    for c in job["clips"]:
        if not c.get("keep", True):
            continue
        x = c["peak"] / dur * W
        marks += f'<a href="#c{c["id"]}"><line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{H}" class="mk"/>' \
                 f'<text x="{x:.1f}" y="14" class="ml">{c["id"]}</text></a>'
    url = job.get("url") or ""
    cards = ""
    for c in job["clips"]:
        if not c.get("keep", True):
            continue
        f = c.get("files", {})
        link = ""
        if "youtu" in url:
            sep = "&" if "?" in url else "?"
            link = f' · <a href="{html.escape(url)}{sep}t={int(c["start"])}s" target="_blank">open in VOD</a>'
        vids = ""
        if f.get("short"):
            vids += f'<video class="v" src="{f["short"]}#t=0.5" controls preload="metadata"></video>'
        if f.get("wide"):
            vids += f'<video class="w" src="{f["wide"]}" controls preload="none" poster="{f.get("thumb", "")}"></video>'
        dl = " ".join(f'<a href="{f[k]}" download>{k}.mp4</a>' for k in ("short", "wide") if f.get(k))
        cards += f"""
<article id="c{c['id']}">
  <header><span class="n">#{c['id']}</span><span class="t">{hms(c['start'])} – {hms(c['end'])}</span>
  <span class="s">score {c['score']}</span>{''.join(f'<span class="why">{html.escape(w)}</span>' for w in c['why'])}</header>
  {f'<h3>{html.escape(c["caption"])}</h3>' if c.get('caption') else ''}
  <div class="vids">{vids}</div>
  <footer>{dl}{link}</footer>
</article>"""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stream Clips · Growthforge</title><style>
:root{{--bg:#0B0B0F;--card:#16161C;--line:#26262F;--text:#F5F5F7;--mut:#A1A1AA;--acc:#FF6B2C}}
@font-face{{font-family:'Space Grotesk';src:url(../../fonts/SpaceGrotesk-var.woff2) format('woff2');font-weight:300 700}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,Arial,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:32px 16px 80px}}
.eb{{display:flex;align-items:center;gap:10px;font:500 11px 'Space Grotesk',Arial;letter-spacing:.2em;text-transform:uppercase;color:var(--mut);margin-bottom:12px}}
.eb:before{{content:"";width:32px;height:1.5px;background:var(--acc)}}.eb b{{color:var(--text);letter-spacing:-.02em;font-weight:700}}.eb b i{{font-style:normal;color:var(--acc)}}
h1{{font:700 44px/.95 'Space Grotesk',Arial;letter-spacing:-.055em;margin:0 0 8px}}h1 em{{font-style:normal;color:var(--acc)}}
.sub{{color:var(--mut);margin:0 0 20px;word-break:break-all}}
.tl{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px;margin-bottom:24px}}
.tl svg{{width:100%;height:120px;display:block}}
.tl polyline{{fill:none;stroke:var(--mut);stroke-width:1.2;opacity:.6}}
.mk{{stroke:var(--acc);stroke-width:2}}.ml{{fill:var(--acc);font:700 12px 'Space Grotesk',Arial;text-anchor:middle}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:18px}}
@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
article{{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:14px;scroll-margin-top:16px}}
header{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}}
.n{{border:1px solid var(--line);border-radius:99px;padding:1px 10px;font:700 13px 'Space Grotesk',Arial}}.t{{font:700 15px 'Space Grotesk',Arial;font-variant-numeric:tabular-nums}}
.s{{color:var(--mut)}}.why{{border:1px solid var(--line);color:var(--mut);border-radius:99px;padding:0 9px;font-size:12.5px}}
h3{{margin:2px 0 10px;font:700 18px 'Space Grotesk',Arial;letter-spacing:-.03em}}
.vids{{display:flex;gap:10px;align-items:flex-start}}
.vids video{{background:#000;border-radius:12px;min-width:0}}.v{{width:34%;aspect-ratio:9/16}}.w{{flex:1;aspect-ratio:16/9}}
footer{{margin-top:10px;display:flex;gap:14px;flex-wrap:wrap}}a{{color:var(--acc)}}
.how{{color:var(--mut);margin-top:28px;font-size:14px}}code{{color:var(--text)}}
</style></head><body><main>
<div class="eb">Clipper by <b>GROWTH<i>FORGE</i></b></div>
<h1>Stream <em>clips.</em></h1>
<p class="sub">{html.escape(Path(job['vod']).name)} · {hms(dur)} · {len([c for c in job['clips'] if c.get('keep', True)])} clips · signals: {html.escape(', '.join(job.get('signals', [])) or 'none')}</p>
<div class="tl"><svg viewBox="0 0 {W} {H}" preserveAspectRatio="none"><polyline points="{pts}"/>{marks}</svg></div>
<div class="grid">{cards}</div>
<p class="how">Want a clip longer, shorter, captioned or gone? Edit <code>highlights.json</code> in this folder
(<code>start</code>, <code>end</code>, <code>caption</code>, <code>"keep": false</code>), then run
<code>python clip.py --render "{html.escape(str(outdir / 'highlights.json'))}"</code>. Timestamps for the VOD description are in <code>timestamps.txt</code>.</p>
</main></body></html>"""
    (outdir / "index.html").write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(description="Find and cut highlights from a stream VOD.")
    ap.add_argument("input", nargs="?", help="VOD file or YouTube/Twitch URL")
    ap.add_argument("--top", type=int, default=10, help="how many auto-detected clips (default 10, 0 = only --at)")
    ap.add_argument("--len", type=float, default=30, help="clip length in seconds (default 30)")
    ap.add_argument("--pre", type=float, default=0.7, help="share of the clip before the peak (default 0.7 = build-up + reaction)")
    ap.add_argument("--at", nargs="*", default=[], help="extra times to clip, e.g. 1:02:30 2:14:05")
    ap.add_argument("--layout", choices=["fit", "cam", "crop", "none"], default="fit", help="Short layout (default fit)")
    ap.add_argument("--cam", default="1394,764,480,270", help="webcam box in the 1920x1080 stream: x,y,w,h")
    ap.add_argument("--caption", default="", help="caption on every Short (per-clip captions: edit highlights.json)")
    ap.add_argument("--chip", default="", help="small tag above captions, e.g. \"GTA VI · LIVE\" (empty = none)")
    ap.add_argument("--no-wide", action="store_true", help="skip the 16:9 clips, Shorts only")
    ap.add_argument("--chat", help="chat replay file (auto-found next to the VOD if yt-dlp downloaded it)")
    ap.add_argument("--chat-lag", type=float, default=6, help="seconds chat lags behind the moment (default 6)")
    ap.add_argument("--skip-start", type=float, default=120, help="ignore the first N seconds (starting screen), default 120")
    ap.add_argument("--out", help="output folder (default clipper/out/<vod name>)")
    ap.add_argument("--find-only", action="store_true", help="just list the moments, don't cut")
    ap.add_argument("--render", metavar="JSON", help="re-cut from an edited highlights.json")
    ap.add_argument("--only", nargs="*", type=int, help="with --render: only these clip numbers")
    return ap


def main():
    for st in (sys.stdout, sys.stderr):  # stream titles often have emoji; a Windows console can't print them
        try:
            st.reconfigure(errors="replace")
        except Exception:
            pass
    ap = build_parser()
    a = ap.parse_args()

    if a.render:
        render(json.loads(Path(a.render).read_text(encoding="utf-8")), set(a.only or []))
        return
    if not a.input:
        ap.print_help()
        return
    try:
        run_vod(a)
    except RuntimeError as e:
        sys.exit(str(e))


def run_vod(a):
    """Find + cut highlights. Returns the output folder. Raises RuntimeError on bad input."""
    url = None
    if re.match(r"https?://", a.input):
        url = a.input
        vod = fetch_audio(url, VODS)
    else:
        vod = Path(a.input).expanduser().resolve()
        if not vod.exists():
            raise RuntimeError(f"Not found: {vod}")

    dur, w, h, has_audio = probe(vod)
    print(f"{vod.name}: {hms(dur)}, {w}x{h}{'' if has_audio else ', NO AUDIO'}")
    chat = Path(a.chat) if a.chat else find_chat_file(vod)
    if chat:
        print(f"  using chat replay: {chat.name}")

    clips = []
    signals = []
    curve = []
    if a.top > 0:
        score, parts, nmsg = analyse(vod, dur, has_audio, chat, a.chat_lag)
        signals = [k for k in ("audio", "chat") if k in parts]
        clips = pick_peaks(score, parts, a.top, a.len, a.pre, dur, min(a.skip_start, dur / 4))
        # downsample the score curve to ~1000 points for the review page
        step = max(1, len(score) // 1000)
        curve = [round(float(np.max(score[i:i + step])), 2) for i in range(0, len(score), step)]
    for t in a.at:
        p = parse_time(t)
        start = max(0.0, p - a.len * a.pre)
        clips.append({"peak": p, "start": round(start, 1), "end": round(min(dur, start + a.len), 1),
                      "score": 0, "why": ["you marked it"]})
    if not clips:
        raise RuntimeError("No highlights stood out. Try --at with times, or check the VOD has audio.")

    clips.sort(key=lambda c: c["start"])
    for i, c in enumerate(clips, 1):
        c["id"] = i
        c["caption"] = a.caption
        c["keep"] = True

    print("\nHighlights:")
    for c in clips:
        print(f"  #{c['id']:>2}  {hms(c['start']):>8} - {hms(c['end']):<8}  score {c['score']:>5}  {', '.join(c['why'])}")

    outdir = Path(a.out) if a.out else REELS / re.sub(r"[^\w\- ]+", "", vod.stem)[:60].strip()
    job = {"vod": str(vod), "url": url, "outdir": str(outdir.resolve()), "duration": dur, "width": w, "height": h,
           "layout": a.layout, "cam": [int(v) for v in a.cam.split(",")], "wide": not a.no_wide,
           "chip": a.chip, "signals": signals, "curve": curve, "clips": clips, "sections": bool(url)}
    if a.find_only:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "highlights.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
        write_timestamps(job, outdir)
        print(f"\nSaved {outdir / 'highlights.json'}. Edit it, then: python clip.py --render \"{outdir / 'highlights.json'}\"")
        return outdir
    print()
    render(job)
    if url:  # the audio was only needed to find the moments; each clip has its own video now
        try:
            vod.unlink()
        except OSError:
            pass
    return outdir


if __name__ == "__main__":
    main()
