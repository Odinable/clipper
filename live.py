"""Live clipper: watches your stream WHILE you're live and makes reels as things happen.

  python live.py https://youtube.com/watch?v=LIVE_ID     watch your YouTube live stream (+ its chat)
  python live.py rtmp://... / srt://... / udp://...      any stream ffmpeg can read
  python live.py VOD.mp4 --simulate                      play a file at real speed, to test

While it runs:
  - it listens to the stream's audio (and chat) and auto-clips the hype moments
  - press ENTER in this window = "clip that" (the last ~25 s)
  - anyone typing !clip in chat does the same (rate-limited by the cooldown)
Each reel is ready ~10-20 s after the moment, in out/live-<date>/, and the
review page there (index.html) refreshes itself.
"""
import argparse, json, os, queue, re, shutil, subprocess, sys, threading, time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

import clip as C

HOP = C.HOP
SR = 8000


class Live:
    def __init__(self, a):
        self.a = a
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        self.outdir = Path(a.out) if a.out else C.REELS / f"live-{stamp}"
        self.segdir = self.outdir / ".buffer"
        self.segdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.now = 0.0                       # stream clock (seconds of audio received)
        self.segs = []                       # [(path, start, end)] in stream time
        self.db = deque(maxlen=int(300 / HOP))
        self.chat = deque(maxlen=int(600 / HOP))
        self.hype = deque(maxlen=int(600 / HOP))
        self.chat_pending = [0, 0]           # msgs / hype since last HOP
        self.has_chat = False
        self.curve = []
        self.event = None                    # {"peak": t, "score": s, "t0": t}
        self.next_ok = 0.0                   # no new auto clip before this stream time
        self.jobs = queue.Queue()
        self.next_id = 1
        self.proc = None
        self.cutting = None
        self.rev = 0                         # bumps whenever a reel is added/changed
        self.pending = []
        self.done = threading.Event()
        self.job = {"vod": "LIVE " + stamp, "url": a.source if "youtu" in a.source else None,
                    "outdir": str(self.outdir.resolve()), "duration": 0, "width": 1920, "height": 1080,
                    "layout": a.layout, "cam": [int(v) for v in a.cam.split(",")], "wide": not a.no_wide, "chip": a.chip,
                    "signals": ["audio"], "curve": [], "clips": []}

    # ------------------------------------------------------------ ingest
    def source_urls(self):
        """The stream's media address(es). YouTube live usually serves video and audio as two
        separate feeds, so this can return [video_url, audio_url]."""
        s = self.a.source
        if re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", s):
            cmd = [*C.ytdlp_cmd(), "-g", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", s]
            if shutil.which("node"):
                cmd[len(C.ytdlp_cmd()):len(C.ytdlp_cmd())] = ["--js-runtimes", "node"]
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=C.NOWIN)
            urls = [u for u in r.stdout.strip().splitlines() if u.startswith("http")]
            if r.returncode or not urls:
                err = [l for l in r.stderr.splitlines() if "ERROR" in l]
                print("  couldn't open the stream yet (is it live?). Retrying in 15 s.")
                if err:
                    print("   ", err[-1].replace("ERROR: ", "")[:200])
                return None
            return urls[:2]
        return [s]

    def ingest(self):
        """One ffmpeg: copies the stream into 2 s .ts segments (the rolling buffer)
        and pipes mono 8 kHz audio to us for analysis. Reconnects if the stream drops."""
        n_try = 0
        seg_base = 0
        while not self.done.is_set():
            urls = self.source_urls()
            if not urls:
                n_try += 1
                if n_try > 40 or self.a.simulate:
                    break
                for _ in range(30):
                    if self.done.is_set():
                        break
                    time.sleep(0.5)
                continue
            n_try = 0
            try:
                _, w, h, _ = C.probe(urls[0])
                self.job["width"], self.job["height"] = w, h
            except (SystemExit, RuntimeError):
                pass
            listfile = self.segdir / f"list{seg_base}.csv"
            aud = len(urls) - 1                      # input index that carries the audio
            cmd = [C.FF, "-hide_banner", "-nostats", "-loglevel", "info"]
            for u in urls:
                if self.a.simulate:
                    cmd += ["-re"]
                elif u.startswith("http"):
                    cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
                cmd += ["-i", u]
            if len(urls) > 1:
                cmd += ["-copyts"]                   # keep both feeds on YouTube's shared clock = in sync
            cmd += ["-map", "0:v:0", "-map", f"{aud}:a:0", "-c", "copy", "-f", "segment", "-segment_time", "2",
                    "-segment_list", str(listfile), "-segment_list_type", "csv", "-segment_list_flags", "live",
                    "-segment_start_number", str(seg_base), str(self.segdir / "s%06d.ts"),
                    "-map", f"{aud}:a:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=C.NOWIN)
            self.proc = p
            self.audio_start = None
            threading.Thread(target=self.read_ffmpeg_log, args=(p, aud), daemon=True).start()
            t_watch = threading.Thread(target=self.watch_segments, args=(listfile, p, self.now), daemon=True)
            t_watch.start()
            step = int(SR * HOP) * 2
            buf = b""
            print(f"  connected{' (separate video + audio feeds)' if len(urls) > 1 else ''}. Watching...\n")
            while True:
                chunk = p.stdout.read(step)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= step:
                    a = np.frombuffer(buf[:step], dtype=np.int16).astype(np.float32)
                    buf = buf[step:]
                    self.on_audio(20 * np.log10(np.sqrt((a ** 2).mean()) + 1.0) - 90.309)
            p.wait()
            t_watch.join(timeout=5)
            if self.a.simulate or self.done.is_set():
                break
            print("  stream dropped; reconnecting...")
            seg_base += 100000
            time.sleep(5)
        self.done.set()

    def read_ffmpeg_log(self, p, aud):
        """ffmpeg prints each input's start time; the audio one anchors our clock."""
        cur = None
        for raw in p.stderr:
            line = raw.decode("utf-8", "ignore").rstrip()
            m = re.match(r"Input #(\d+)", line)
            if m:
                cur = int(m[1])
            m = re.search(r"start: (-?[\d.]+)", line)
            if m and cur == aud and self.audio_start is None:
                self.audio_start = float(m[1])
            if re.search(r"error|failed|invalid", line, re.I) and "Last message repeated" not in line:
                print("   ffmpeg:", line[:200])

    def watch_segments(self, listfile, proc, clock0):
        """Tail ffmpeg's segment list; keep each finished segment's range on our stream clock
        (seconds of audio received, which started at clock0 for this connection)."""
        pos = 0
        ref = None
        while True:
            alive = proc.poll() is None
            if listfile.exists():
                with open(listfile, encoding="utf-8") as f:
                    f.seek(pos)
                    data = f.read()
                    # only consume complete lines
                    cut = data.rfind("\n") + 1
                    pos += len(data[:cut].encode("utf-8"))
                for line in data[:cut].splitlines():
                    name, s, e = line.rsplit(",", 2)
                    s, e = float(s), float(e)
                    s = max(s, e - 15)   # with -copyts the very first entry reports start 0
                    if ref is None:
                        ref = self.audio_start if self.audio_start is not None else s
                    with self.lock:
                        self.segs.append((self.segdir / name, s - ref + clock0, e - ref + clock0))
            if not alive:
                break
            time.sleep(0.5)

    # ------------------------------------------------------------ chat
    def chat_youtube(self):
        """yt-dlp downloads a live stream's chat as it happens; we tail the file it writes."""
        cdir = self.segdir / "chat"
        cdir.mkdir(exist_ok=True)
        p = subprocess.Popen([*C.ytdlp_cmd(), "--skip-download", "--write-subs",
                              "--sub-langs", "live_chat", "-o", str(cdir / "chat"), self.a.source],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C.NOWIN)
        f, pos = None, 0
        while not self.done.is_set():
            if f is None:
                hits = list(cdir.glob("chat*live_chat*"))
                if not hits:
                    time.sleep(2)
                    continue
                f = hits[0]
                self.has_chat = True
                self.job["signals"] = ["audio", "chat"]
                print("  reading live chat")
            try:
                with open(f, "rb") as fh:
                    fh.seek(pos)
                    data = fh.read()
            except FileNotFoundError:   # .part renamed when it finishes
                f = None
                continue
            cut = data.rfind(b"\n") + 1
            pos += cut
            for line in data[:cut].splitlines():
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                for act in o.get("replayChatItemAction", {}).get("actions", []):
                    item = act.get("addChatItemAction", {}).get("item", {})
                    r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                    if r:
                        text = "".join(x.get("text", "") for x in r.get("message", {}).get("runs", []))
                        self.on_chat(text, "liveChatPaidMessageRenderer" in item)
            time.sleep(1)
        p.terminate()

    def chat_replay(self, path):
        """--simulate: feed a saved chat replay in at the right moments."""
        msgs = sorted(C.load_chat(path))
        self.has_chat = True
        self.job["signals"] = ["audio", "chat"]
        i = 0
        while not self.done.is_set() and i < len(msgs):
            while i < len(msgs) and msgs[i][0] <= self.now:
                self.on_chat(msgs[i][1], msgs[i][2])
                i += 1
            time.sleep(0.2)

    def on_chat(self, text, paid):
        with self.lock:
            self.chat_pending[0] += 1
            if C.HYPE.search(text or ""):
                self.chat_pending[1] += 1
            if paid:
                self.chat_pending[1] += 3
        if re.match(r"\s*!clip\b", text or "", re.I):
            self.manual("chat !clip")

    # ------------------------------------------------------------ detection
    @staticmethod
    def z(hist, recent, base_len, floor):
        """Recent level vs the median of the past (excluding the recent part), in robust SDs."""
        h = np.asarray(hist, dtype=np.float32)
        if len(h) < recent + 20:
            return 0.0
        past = h[-(base_len + recent):-recent]
        med = float(np.median(past))
        mad = max(float(np.median(np.abs(past - med))) * 1.4826, floor)
        return (float(h[-recent:].mean()) - med) / mad

    def on_audio(self, db):
        with self.lock:
            self.now += HOP
            now = self.now
            self.db.append(db)
            self.chat.append(self.chat_pending[0])
            self.hype.append(self.chat_pending[1])
            self.chat_pending = [0, 0]
        audio = C.squash(np.array(0.6 * self.z(self.db, 4, 240, 2.0) + 0.4 * self.z(self.db, 16, 224, 2.0)))
        score = float(audio)
        chat = 0.0
        if self.has_chat:
            # chat reacts late: compare the last 10 s of chat to the 5 minutes before it
            chat = float(C.squash(np.array(0.5 * self.z(self.chat, 20, 580, 0.15) + 0.8 * self.z(self.hype, 20, 580, 0.1))))
            score += 1.3 * chat
            when = now - self.a.chat_lag
        else:
            when = now
        self.curve.append(score)
        if int(now / HOP) % 120 == 0:
            print(f"\r  live {C.hms(now)}  clips {len(self.job['clips'])}  queued {self.jobs.qsize() + len(self.pending)}   ", end="", flush=True)

        if now < self.a.warmup:
            return
        ev = self.event
        if ev is None:
            if score >= self.a.sensitivity and now >= self.next_ok:
                why = []
                if audio > 2: why.append("loud moment")
                if chat > 2: why.append("chat went off")
                self.event = {"peak": now if audio >= chat else when, "score": score, "t0": now, "why": why or ["spike"]}
        else:
            if score > ev["score"]:
                ev.update(peak=now if audio >= chat else when, score=score)
                if chat > 2 and "chat went off" not in ev["why"]: ev["why"].append("chat went off")
                if audio > 2 and "loud moment" not in ev["why"]: ev["why"].append("loud moment")
            if score < self.a.sensitivity * 0.5 or now - ev["t0"] > 12:
                self.event = None
                self.add_clip(ev["peak"], self.a.len * self.a.pre, ev["score"], ev["why"])

    def manual(self, why):
        with self.lock:
            now = self.now
            ready = bool(self.segs)
        if not ready:
            print("  clip ignored: the stream isn't connected yet")
            return
        # you press after the moment: grab the last (len - 5) s plus 5 s more
        self.add_clip(now - 5, self.a.len - 5, 0, [why])

    def add_clip(self, peak, before, score, why):
        start = max(0.0, peak - before)
        end = start + self.a.len
        self.next_ok = end + self.a.cooldown
        with self.lock:
            cid, self.next_id = self.next_id, self.next_id + 1
        c = {"id": cid,
             "peak": round(peak, 1), "start": round(start, 1), "end": round(end, 1),
             "score": round(score, 1), "why": why, "caption": self.a.caption, "keep": True}
        print(f"\n  >> moment at {C.hms(peak)} ({', '.join(why)}), reel coming once the stream passes {C.hms(end)}")
        self.jobs.put(c)

    # ------------------------------------------------------------ cutting
    def cutter(self):
        while not (self.done.is_set() and self.jobs.empty() and not self.pending):
            try:
                self.pending.append(self.jobs.get(timeout=0.5))
            except queue.Empty:
                pass
            with self.lock:
                covered = self.segs[-1][2] if self.segs else 0.0
            for c in list(self.pending):
                if covered >= c["end"] + 3 or self.done.is_set():
                    self.pending.remove(c)
                    self.cut(c)
            self.cleanup()

    def cut(self, c):
        with self.lock:
            segs = [s for s in self.segs if s[2] > c["start"] - 6 and s[1] < c["end"] + 4]
        if not segs:
            print(f"\n  !! no video buffered for {C.hms(c['start'])}, skipped")
            return
        c["end"] = min(c["end"], segs[-1][2])
        base = f"{c['id']:02d}_{C.hms(c['start']).replace(':', '-')}"
        src = self.outdir / "sources" / f"{base}.ts"
        src.parent.mkdir(exist_ok=True)
        with open(src, "wb") as out:                      # MPEG-TS segments join by appending
            for path, _, _ in segs:
                with open(path, "rb") as f:
                    shutil.copyfileobj(f, out)
        c["src"] = str(src.relative_to(self.outdir))
        c["src_offset"] = round(segs[0][1], 3)
        t0 = time.time()
        self.cutting = c
        C.render_one(self.job, c, self.outdir, label=f"\n  cutting #{c['id']}")
        self.job["clips"].append(c)
        self.cutting = None
        self.save()
        print(f"  reel #{c['id']} ready in {time.time() - t0:.0f}s -> {c['files'].get('short') or c['files'].get('wide')}")
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass

    def cleanup(self):
        """Drop buffered segments older than --buffer seconds (clips keep their own copy)."""
        with self.lock:
            keep_from = self.now - self.a.buffer
            if self.pending or not self.jobs.empty():
                keep_from = min([keep_from] + [c["start"] - 10 for c in self.pending])
            old = [s for s in self.segs if s[2] < keep_from]
            self.segs = [s for s in self.segs if s[2] >= keep_from]
        for path, _, _ in old:
            try:
                path.unlink()
            except OSError:
                pass

    def stop(self):
        self.done.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def pending_list(self):
        return sorted(list(self.pending) + list(self.jobs.queue), key=lambda c: c["start"])

    def save(self):
        self.rev += 1
        self.job["duration"] = max(self.now, 1)
        step = max(1, len(self.curve) // 1000)
        self.job["curve"] = [round(max(self.curve[i:i + step]), 2) for i in range(0, len(self.curve), step)]
        self.job["clips"].sort(key=lambda c: c["start"])
        (self.outdir / "highlights.json").write_text(json.dumps(self.job, indent=2), encoding="utf-8")
        C.write_review(self.job, self.outdir)
        C.write_timestamps(self.job, self.outdir)
        page = self.outdir / "index.html"
        reload = ("<script>setInterval(()=>{if(![...document.querySelectorAll('video')].some(v=>!v.paused))"
                  "location.reload()},15000)</script></body>")
        page.write_text(page.read_text(encoding="utf-8").replace("</body>", reload), encoding="utf-8")

    # ------------------------------------------------------------ run
    def keyboard(self):
        while not self.done.is_set():
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                return
            self.manual("you hit clip")

    def run(self):
        print(f"Live clipper -> {self.outdir}  ({'NVENC GPU' if C.NVENC else 'CPU'} encoding)")
        self.save()
        threads = [threading.Thread(target=self.ingest, daemon=True),
                   threading.Thread(target=self.cutter, daemon=True)]
        if sys.stdin and sys.stdin.isatty():
            threads.append(threading.Thread(target=self.keyboard, daemon=True))
        if self.a.chat:
            threads.append(threading.Thread(target=self.chat_replay, args=(self.a.chat,), daemon=True))
        elif "youtu" in self.a.source and not self.a.no_chat:
            threads.append(threading.Thread(target=self.chat_youtube, daemon=True))
        for t in threads:
            t.start()
        try:
            while not self.done.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n  stopping; finishing reels already queued...")
            self.stop()
        self.stop()                          # also ends ffmpeg when stopped from the app
        threads[1].join()
        self.save()
        shutil.rmtree(self.segdir, ignore_errors=True)
        print(f"\nStream over: {len(self.job['clips'])} reels in {self.outdir}")


def build_parser():
    ap = argparse.ArgumentParser(description="Make reels from a stream while it's live.")
    ap.add_argument("source", help="YouTube live URL, rtmp/srt/udp URL, or a file with --simulate")
    ap.add_argument("--simulate", action="store_true", help="play a file at real speed (testing)")
    ap.add_argument("--len", type=float, default=30, help="reel length in seconds (default 30)")
    ap.add_argument("--pre", type=float, default=0.7, help="share of the reel before the moment (default 0.7)")
    ap.add_argument("--sensitivity", type=float, default=6, help="lower = more reels (default 6; try 4-9)")
    ap.add_argument("--cooldown", type=float, default=15, help="min gap after a reel ends before the next auto one (s)")
    ap.add_argument("--warmup", type=float, default=90, help="learn your normal volume for N s before clipping")
    ap.add_argument("--layout", choices=["fit", "cam", "crop", "none"], default="fit")
    ap.add_argument("--cam", default="1394,764,480,270", help="webcam box in the 1920x1080 stream: x,y,w,h")
    ap.add_argument("--caption", default="", help="caption on every reel")
    ap.add_argument("--chip", default="", help="small tag above captions (empty = none)")
    ap.add_argument("--no-wide", action="store_true", help="reels only, skip the 16:9 clips")
    ap.add_argument("--chat", help="--simulate: a saved chat replay to play along")
    ap.add_argument("--no-chat", action="store_true", help="don't read YouTube chat")
    ap.add_argument("--chat-lag", type=float, default=6)
    ap.add_argument("--buffer", type=float, default=300, help="seconds of stream kept on disk (default 300)")
    ap.add_argument("--out", help="output folder (default out/live-<date>)")
    return ap


def main():
    a = build_parser().parse_args()
    if a.simulate and not a.chat:
        found = C.find_chat_file(Path(a.source))
        a.chat = str(found) if found else None
    Live(a).run()


if __name__ == "__main__":
    main()
