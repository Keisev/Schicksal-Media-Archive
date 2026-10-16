<div align="center">

# 🎬 Schicksal Media Archive

**A local-first YouTube download & subtitle-burning workstation with a modern web UI — packaged as a true desktop app.**

Successor to *Gacha PV YT-DLP GUI v4.0* (CustomTkinter), rebuilt from the ground up as a **FastAPI backend + vanilla HTML/CSS/JS frontend**, wrapped in a native window (pywebview + QtWebEngine) and shipped as a portable **Linux AppImage**.

</div>

---

## ✨ Features

### 📥 Download & media control
- Download YouTube videos with a **video quality selector** (per-resolution format buckets, or “best available”).
- **Audio source switching**: use the original video’s audio track or grab audio from a *different* video (full track listing with bitrates).
- High-quality re-encode pipeline: **H.264 1080p, CRF 18, ~10 Mbps, AAC 192 kbps, `+faststart`** — ready for Twitter/X, Discord and YouTube re-uploads.
- One-click **stop/cancel** with safe subprocess teardown.

### 💬 Subtitles, your way
- Sources: **internal**, **external (URL)**, **external (uploaded `.srt`)** and **auto-generated** (real language list read from YouTube, not hardcoded).
- Processing modes: **burn** (with `force_style` font styling), **embed** as MP4 tracks (`mov_text`, multiple languages supported), or **keep the `.srt`** next to the video.
- Multi-language embedding with proper ISO-639-2 track metadata.

### ⚡ Real-time interface
- **WebSocket-powered log console** (`/ws/logs`) with history replay, keep-alive pings, exponential-backoff reconnection and a REST fallback.
- **Live progress bar** parsed from yt-dlp output (percent, size, speed, ETA) plus stage events (`video` → `audio` → `merge`) and an indeterminate bar during FFmpeg processing.
- Server-side **folder browser** for the output directory, persistent config, and dark-themed UI with independent scroll areas.

### 🍪 Anti-bot cookies
- YouTube’s “sign in to confirm you’re not a bot” wall? Handled.
- Cookie modes: **from your browser** (`--cookies-from-browser firefox:profile…`) or **uploaded `cookies.txt`** (Netscape format, validated on upload).
- Cookies and config persist in your user data directory — never inside the AppImage.

### 📦 Desktop packaging
- **Linux**: portable `.AppImage` (PyInstaller `--onedir` + linuxdeploy), bundles Python, yt-dlp and static ffmpeg/ffprobe builds.
- **Windows**: build script included (`build_windows.ps1`) — binary coming in a future release.
- Read-only-safe runtime: all writable data lives in `~/.local/share/SchicksalMediaArchive/` (Linux) or `%APPDATA%\SchicksalMediaArchive` (Windows).

---

##  Quick start

### Option A — AppImage (recommended, Linux)
1. Grab `Schicksal_Media_Archive-<version>-x86_64.AppImage` from [Releases](../../releases).
2. Make it executable and run it:
   ```bash
   chmod +x Schicksal_Media_Archive-1.0.0-x86_64.AppImage
   ./Schicksal_Media_Archive-1.0.0-x86_64.AppImage
   ```
3. A native window opens serving the UI at `http://127.0.0.1:8000`.

> **No FUSE?** Some distros ship FUSE 3 only. Either install `fuse2`/`libfuse2`, or run:
> ```bash
> ./Schicksal_Media_Archive-1.0.0-x86_64.AppImage --appimage-extract-and-run
> ```

### Option B — From source (dev server)
```bash
git clone https://github.com/Keisev/Schicksal-Media-Archive.git
cd Schicksal-Media-Archive
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# System binaries required in dev mode:
#   sudo pacman -S yt-dlp ffmpeg      (Arch/CachyOS)
#   sudo apt install yt-dlp ffmpeg    (Debian/Ubuntu)

python app.py        # then open http://127.0.0.1:8000
```

### Option C — Desktop window from source
```bash
pip install -r requirements.txt -r requirements-linux.txt   # adds PyQt6/WebEngine on Linux
python main.py                                             # uvicorn in a thread + pywebview window
```

---

## 🧭 Typical workflow
1. Paste a YouTube URL → **Get Info** (title, video qualities, audio tracks, subtitle languages).
2. Pick **video quality**, **audio source/track**, and **subtitle mode** (burn / embed / keep).
3. Choose the **output directory** with the built-in browser.
4. Hit the floating **Start Download** button and watch logs + progress in real time.
5. Find your file in the output dir (plus `.srt` files if you asked to keep them).

---

## 🗂️ Data & configuration locations

| What | Where |
|---|---|
| Config (`ytdlp_webgui_config.json`) | `~/.local/share/SchicksalMediaArchive/` |
| Cookies (`cookies.txt`) | `~/.local/share/SchicksalMediaArchive/` |
| Logs (`ytdlp_webgui.log`) | `~/.local/share/SchicksalMediaArchive/` |
| Temp jobs & uploads | `~/.local/share/SchicksalMediaArchive/temp/`, `.../uploads/` |

---

## 🛠️ Building from source (Linux AppImage)

```bash
./build_linux.sh
```

The script will:
1. Verify/install Python deps (PyInstaller, pywebview, Pillow).
2. Download real binaries: yt-dlp standalone + static ffmpeg/ffprobe (BtbN builds).
3. Run PyInstaller in `--onedir` mode bundling `static/`, yt-dlp and ffmpeg.
4. Assemble the `AppDir` (AppRun, `.desktop`, generated PNG icon).
5. Run linuxdeploy (+ appimagetool fallback) to produce `dist/*.AppImage`.
6. Verify the artifact is extractable and executable.

Artifacts (`AppDir/`, `build_bin/`, `*.AppImage`, …) are git-ignored by design; binaries belong in **Releases**, not in the repo.

---

## 🧱 Project structure

```text
├── app.py               # FastAPI backend: APIs, WebSocket, yt-dlp/ffmpeg orchestration
├── main.py              # Desktop entrypoint: uvicorn thread + pywebview window
├── utils.py             # Frozen-aware path helpers (sys._MEIPASS) & binary resolution
├── static/              # Frontend: index.html, styles.css, app.js (vanilla JS)
├── build_linux.sh       # AppImage pipeline (7 steps)
├── build_windows.ps1    # Windows build script (exe)
├── requirements.txt           # Core deps (FastAPI, uvicorn, websockets, …)
└── requirements-linux.txt     # Linux desktop deps (PyQt6, QtWebEngine, qtpy)
```

---

## 🔐 Security & privacy
- The server binds to **127.0.0.1 only**. It is a personal tool, not a public service — do not expose it to the network.
- `cookies.txt` is equivalent to your YouTube session. Keep it local; it is git-ignored.
- The server-side folder browser and file uploads only make sense because the backend runs on *your* machine.

## ⚖️ Disclaimer
This project is intended for **personal archival of content you have rights to**. Respect YouTube’s Terms of Service and applicable copyright law in your country.

## 🗺️ Roadmap
- [ ] GitHub Actions CI: auto-build AppImage + Windows exe on every `v*` tag.
- [ ] Windows portable `.exe` release.
- [ ] Download queue & multi-job management.
- [ ] Presets (Twitter/X, Discord, archive-grade MKV).

## 🙏 Credits
Built on the shoulders of: [yt-dlp](https://github.com/yt-dlp/yt-dlp), [FFmpeg](https://ffmpeg.org) (static builds by [BtbN](https://github.com/BtbN/FFmpeg-Builds)), [FastAPI](https://fastapi.tiangolo.com), [pywebview](https://pywebview.readthedocs.io), [PyQt6](https://www.riverbankcomputing.com/software/pyqt/), [PyInstaller](https://pyinstaller.org), [linuxdeploy](https://github.com/linuxdeploy/linuxdeploy) & [AppImageKit](https://github.com/AppImage/AppImageKit).

## 📄 License
Not licensed yet — all rights reserved by the author for the time being.
