<p align="center"><img src="brand/mark.png" width="72" alt=""></p>

<h1 align="center">Clipper</h1>
<p align="center"><b>Reels while you stream. Free forever.</b><br>A free Windows app by <a href="https://growthforge.cloud">Growthforge</a></p>

<p align="center"><a href="https://github.com/Odinable/clipper/releases/latest/download/Clipper-Setup.exe"><b>Download for Windows</b></a> · <a href="https://growthforge.cloud/clipper">Website</a></p>

---

Clipper watches your live stream while you play. When you or your chat go off, it cuts that moment into a vertical reel for Shorts, TikTok or Reels, usually within seconds. Point it at a finished stream and it finds every highlight in one go.

- **Live mode:** paste your YouTube live link and play. Reels appear while you stream.
- **Clip it yourself:** press **F9** anywhere on your PC, hit **CLIP THAT**, or have your mods type **`!clip`** in chat.
- **After the stream:** finds the best moments in a finished stream or an OBS recording.
- **Ready to post:** 9:16 reels (full game, webcam + game, or zoomed), a 16:9 copy, optional captions and a tag.
- **Fast:** uses your NVIDIA GPU (NVENC) when there is one, and works without one too.
- **Private:** your streams, chat and reels stay on your PC. The only thing sent to Growthforge is the name and email you sign in with.

## Install

1. Download **[Clipper-Setup.exe](https://github.com/Odinable/clipper/releases/latest/download/Clipper-Setup.exe)** and run it. No admin needed.
2. Windows may say *"Windows protected your PC"*, because Clipper isn't code-signed yet. Click **More info → Run anyway**.
3. Open Clipper from the Start menu and sign in once with your name and email.

Needs Windows 10 or 11. The installer lets you pick the install folder, on any drive. Reels are saved to **Videos\Clipper** unless you pick another folder in the app. To uninstall: **Settings → Apps → Clipper → Uninstall** (your reels are kept).

## How it picks moments

Clipper scores every half-second of the stream:
- **Audio:** sudden loudness compared with the last few minutes, like shouting, laughing, crashes or explosions.
- **Chat:** bursts of messages, hype words and Super Chats. Chat lags behind, so it's shifted back a few seconds.

When the score crosses the line, it clips the build-up before and the reaction after. "How picky" moves that line.

## Run from source

```
pip install -r requirements.txt
pythonw app.py          # the app window
python live.py <url>    # command-line live mode
python clip.py <vod>    # command-line after-stream mode
```

Build the installer with `python packaging/build.py`. The output is `packaging/dist/Clipper-Setup.exe`.

## Privacy

See the privacy page in the app (Privacy, bottom left) and [growthforge.cloud/privacy](https://growthforge.cloud/privacy#clipper). Only clip streams you own or have permission to clip.

## Licence

Clipper's code is MIT-licensed (see `LICENSE`). The installer bundles FFmpeg (GPL v3), yt-dlp and other open-source software. See `THIRD-PARTY-NOTICES.md`. The Growthforge name and logos aren't covered by the licence.

Questions or bugs: **growthforge.cloud@gmail.com** · [growthforge.cloud](https://growthforge.cloud)
