import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from utils import get_binary_path, resource_path

# ============================================================
# Rutas y configuración básica (Compatible con PyInstaller/AppImage)
# ============================================================

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Entorno empaquetado (AppImage / .exe)
    BUNDLE_DIR = Path(sys._MEIPASS)
    
    # Carpeta de datos de usuario (escritura)
    if os.name == 'nt':
        DATA_DIR = Path(os.getenv('APPDATA', Path.home() / "AppData" / "Roaming")) / "SchicksalMediaArchive"
    else:
        DATA_DIR = Path.home() / ".local" / "share" / "SchicksalMediaArchive"
        
    # Rutas absolutas a los binarios empaquetados
    YTDLP_BIN = str(BUNDLE_DIR / "yt-dlp")
    FFMPEG_BIN = str(BUNDLE_DIR / "ffmpeg")
    FFPROBE_BIN = str(BUNDLE_DIR / "ffprobe")
else:
    # Entorno de desarrollo (python app.py)
    BUNDLE_DIR = Path(__file__).resolve().parent
    DATA_DIR = BUNDLE_DIR
    
    # En desarrollo usamos los binarios del PATH del sistema
    YTDLP_BIN = "yt-dlp"
    FFMPEG_BIN = "ffmpeg"
    FFPROBE_BIN = "ffprobe"

STATIC_DIR = BUNDLE_DIR / "static"
UPLOAD_DIR = DATA_DIR / "uploads"
TEMP_DIR = DATA_DIR / "temp"
CONFIG_FILE = DATA_DIR / "ytdlp_webgui_config.json"
COOKIES_FILE = DATA_DIR / "cookies.txt"
LOG_FILE = DATA_DIR / "ytdlp_webgui.log"

for directory in (DATA_DIR, UPLOAD_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# ============================================================
# Configuración de logging (archivo en carpeta de datos + consola)
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("ytdlp-webgui")


# ============================================================
# Estado global
# ============================================================

app = FastAPI(title="Schicksal Media Archive")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.busy = False
        self.cancel_event = threading.Event()
        self.current_process = None
        self.last_output_dir = str(Path.home() / "Videos" / "YT-DLP")
        self.cookies_mode = "none"      # "none" | "browser" | "file"
        self.cookies_browser = "firefox"
        self.cookies_profile = None


state = AppState()

LOGS: List[Dict[str, Any]] = []
LOG_ID = 0
LOG_LOCK = threading.Lock()
MAX_LOGS = 3000


# ============================================================
# WebSockets: ConnectionManager + broadcast desde hilos sync
# ============================================================

class ConnectionManager:
    """Mantiene las conexiones WebSocket activas y hace broadcast de eventos."""

    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()
        self.lock = asyncio.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.active.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            self.active.discard(websocket)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        async with self.lock:
            targets = list(self.active)

        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                # Conexión muerta: la retiramos sin interrumpir el resto
                async with self.lock:
                    self.active.discard(ws)

    def broadcast_threadsafe(self, payload: Dict[str, Any]) -> None:
        """Permite a los hilos de trabajo (yt-dlp/ffmpeg) emitir eventos.

        Se llama desde hilos no-async; reprograma el broadcast en el
        event loop de uvicorn mediante `call_soon_threadsafe`.
        """
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), loop)
        except RuntimeError:
            pass


ws_manager = ConnectionManager()


def get_busy_snapshot() -> Dict[str, Any]:
    with state.lock:
        return {"busy": state.busy}

def notify_status() -> None:
    """Emite el estado busy actual a todos los clientes WebSocket."""
    ws_manager.broadcast_threadsafe(
        {"type": "status", "data": get_busy_snapshot()}
    )

def send_event(event: str, data: Dict[str, Any]) -> None:
    """Envía un evento JSON por WebSocket desde cualquier hilo."""
    ws_manager.broadcast_threadsafe({"type": event, "data": data})


def add_log(message: str, level: str = "info") -> None:
    global LOG_ID

    level_map = {
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "success": logging.INFO,
    }

    logger.log(level_map.get(level.lower(), logging.INFO), message)

    entry = None
    with LOG_LOCK:
        LOG_ID += 1
        entry = {
            "id": LOG_ID,
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        LOGS.append(entry)

        if len(LOGS) > MAX_LOGS:
            del LOGS[: len(LOGS) - MAX_LOGS]

    # Broadcast del log a todos los clientes WebSocket conectados
    if entry is not None:
        ws_manager.broadcast_threadsafe({"type": "log", "data": entry})

    # El estado (busy) cambia junto con ciertos logs: lo sincronizamos
    # también para que el frontend reactive/bloquee botones en tiempo real.
    if level in ("success", "error", "warning"):
        ws_manager.broadcast_threadsafe(
            {"type": "status", "data": get_busy_snapshot()}
        )


# ============================================================
# Modelos de entrada
# ============================================================

class InfoRequest(BaseModel):
    url: str
    kind: Literal["main", "audio", "subs"] = "main"


class ConfigPayload(BaseModel):
    output_dir: str


class DownloadRequest(BaseModel):
    video_url: str
    video_title: Optional[str] = None

    output_dir: str

    audio_source: Literal["original", "external"] = "original"
    audio_url: Optional[str] = None
    audio_format_id: Optional[str] = None

    subtitle_type: Literal["none", "internal", "external", "automatic"] = "none"
    subtitle_lang: Optional[str] = None
    subtitle_url: Optional[str] = None

    external_sub_source: Optional[Literal["url", "file"]] = None
    uploaded_srt: Optional[str] = None
    uploaded_srt_original_name: Optional[str] = None

    burn_subtitles: bool = True
    embed_subtitles: bool = False
    keep_srt: bool = False

    video_format_id: Optional[str] = None

class CookiesPayload(BaseModel):
    mode: Literal["none", "browser", "file"] = "none"
    browser: Optional[str] = "firefox"
    profile: Optional[str] = None


# ============================================================
# Utilidades generales
# ============================================================

_DEPENDENCY_CACHE: Optional[Dict[str, Any]] = None


def _startup_info():
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return startupinfo
    return None


def check_dependency(command: List[str]) -> bool:
    try:
        subprocess.run(
            command,
            capture_output=True,
            timeout=10,
            startupinfo=_startup_info(),
        )
        return True
    except Exception:
        return False


def get_dependency_status(force: bool = False) -> Dict[str, Any]:
    global _DEPENDENCY_CACHE

    if _DEPENDENCY_CACHE is not None and not force:
        return _DEPENDENCY_CACHE

    missing = []

    if not check_dependency([get_binary_path("yt-dlp"), "--version"]):
        missing.append("yt-dlp")

    if not check_dependency([get_binary_path("ffmpeg"), "-version"]):
        missing.append("ffmpeg")

    _DEPENDENCY_CACHE = {
        "ok": len(missing) == 0,
        "missing": missing,
    }

    return _DEPENDENCY_CACHE


def is_youtube_url(url: str) -> bool:
    if not url:
        return False

    youtube_patterns = [
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+",
        r"(?:https?://)?(?:www\.)?youtu\.be/[\w-]+",
        r"(?:https?://)?(?:www\.)?youtube\.com/embed/[\w-]+",
        r"(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]+",
    ]

    return any(re.match(pattern, url, re.IGNORECASE) for pattern in youtube_patterns)


def sanitize_filename(filename: str) -> str:
    if not filename:
        return "video_sin_titulo"

    forbidden_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(forbidden_chars, "_", filename)
    sanitized = sanitized.strip().rstrip(".")

    if len(sanitized) > 200:
        sanitized = sanitized[:200]

    return sanitized if sanitized else "video_sin_titulo"


def load_config() -> None:
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            state.last_output_dir = data.get(
                "last_output_dir",
                state.last_output_dir,
            )
            state.cookies_mode = data.get("cookies_mode", "none")
            state.cookies_browser = data.get("cookies_browser", "firefox")
            state.cookies_profile = data.get("cookies_profile", None)
    except Exception:
        pass


def save_config(output_dir: Optional[str] = None) -> None:
    try:
        if output_dir:
            state.last_output_dir = output_dir

        CONFIG_FILE.write_text(
            json.dumps(
                {
                    "last_output_dir": state.last_output_dir,
                    "cookies_mode": state.cookies_mode,
                    "cookies_browser": state.cookies_browser,
                    "cookies_profile": state.cookies_profile,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        add_log(f"No se pudo guardar la configuración: {e}", "warning")


load_config()

def get_cookies_args() -> List[str]:
    """Devuelve los argumentos de cookies para yt-dlp según la configuración."""
    if state.cookies_mode == "browser":
        browser = (state.cookies_browser or "firefox").strip()
        profile = (state.cookies_profile or "").strip()

        target = f"{browser}:{profile}" if profile else browser
        return ["--cookies-from-browser", target]

    if state.cookies_mode == "file":
        if COOKIES_FILE.exists():
            return ["--cookies", str(COOKIES_FILE)]
        add_log(
            "Modo cookies 'file' activo, pero no existe cookies.txt en el proyecto",
            "warning",
        )
        return []

    return []

class JobCancelled(Exception):
    pass


def check_cancelled() -> None:
    if state.cancel_event.is_set():
        raise JobCancelled()


# ============================================================
# Ejecución de comandos
# ============================================================

# yt-dlp: "[download]  45.2% of  120.00MiB at   12.34MiB/s ETA 00:08"
PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<percent>[\d.]+)%\s+of\s+~?(?P<size>\S+)"
    r"(?:\s+at\s+~?(?P<speed>\S+))?(?:\s+ETA\s+(?P<eta>\S+))?",
    re.IGNORECASE,
)

# ffmpeg: "frame= 1234 ... time=00:01:23.45 bitrate= ..."
FFMPEG_TIME_RE = re.compile(r"time=(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")


def parse_progress_line(line: str) -> Optional[Dict[str, Any]]:
    """Interpreta una línea de progreso de yt-dlp y devuelve un dict JSON-safe."""
    match = PROGRESS_RE.search(line)
    if not match:
        return None

    try:
        percent = float(match.group("percent"))
    except (TypeError, ValueError):
        return None

    return {
        "percent": percent,
        "size": match.group("size"),
        "speed": match.group("speed"),
        "eta": match.group("eta"),
        "raw": line,
    }


def run_command(command: List[Any]) -> None:
    check_cancelled()

    pretty_command = " ".join(str(part) for part in command)
    add_log(f"$ {pretty_command}", "info")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=_startup_info(),
    )

    with state.lock:
        state.current_process = process

    try:
        for line in process.stdout:
            if state.cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
                raise JobCancelled()

            line = line.rstrip()
            if not line:
                continue

            # --- Progreso real de yt-dlp -> WebSocket -------------------
            progress = parse_progress_line(line)
            if progress is not None:
                send_event("progress", progress)
                continue  # No inundar los logs con líneas de progreso

            low = line.lower()

            if "error" in low:
                level = "error"
            elif "warning" in low:
                level = "warning"
            else:
                level = "info"

            add_log(line, level)

        return_code = process.wait()

        if state.cancel_event.is_set():
            raise JobCancelled()

        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)

    finally:
        with state.lock:
            state.current_process = None


def get_ytdlp_info(url: str, timeout: int = 120) -> Dict[str, Any]:
    command = [
        get_binary_path("yt-dlp"),
        "--dump-json",
        "--no-playlist",
    ] + get_cookies_args() + [
        url,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            startupinfo=_startup_info(),
        )
    except FileNotFoundError:
        raise RuntimeError("yt-dlp no está instalado o no está en el PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp tardó demasiado obteniendo información")

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Error ejecutando yt-dlp")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("No se pudo interpretar la respuesta JSON de yt-dlp")


# ============================================================
# Extracción de información
# ============================================================

def extract_audio_tracks(info: Dict[str, Any]) -> List[Dict[str, str]]:
    tracks = []
    seen_labels = set()

    for f in info.get("formats", []):
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            format_id = f.get("format_id")
            if not format_id:
                continue

            label = f.get("format_note") or f.get("format") or format_id

            abr = f.get("abr")
            if abr:
                try:
                    label += f" (~{int(abr)}k)"
                except Exception:
                    label += f" (~{abr}k)"

            base_label = label
            counter = 2

            while label in seen_labels:
                label = f"{base_label} ({counter})"
                counter += 1

            seen_labels.add(label)

            tracks.append(
                {
                    "value": format_id,
                    "label": label,
                }
            )

    return tracks


def extract_video_tracks(info: Dict[str, Any]) -> List[Dict[str, str]]:
    """Filtra formatos con video (vcodec != 'none') y los agrupa por
    resolución para el selector de calidad del frontend."""
    # bucket key -> {"height": int|None, "ext": str, "note": str, "ids": [..]}
    buckets: Dict[Any, Dict[str, Any]] = {}

    for f in info.get("formats", []):
        vcodec = f.get("vcodec") or "none"
        if vcodec == "none":
            continue

        format_id = f.get("format_id")
        if not format_id:
            continue

        height = f.get("height")
        ext = f.get("ext") or "mp4"
        acodec = f.get("acodec") or "none"
        note = f.get("format_note") or ""

        # Formatos sin audio no se ofrecen como "listos"; yt-dlp los
        # combinará igualmente con la pista de audio seleccionada.
        key = (height if height else 0, ext)

        bucket = buckets.setdefault(
            key,
            {
                "height": height,
                "ext": ext,
                "note": note,
                "ids": [],
                "has_audio": acodec != "none",
            },
        )

        if acodec != "none":
            bucket["has_audio"] = True
        bucket["ids"].append(format_id)

    tracks: List[Dict[str, str]] = []

    # Opción "mejor calidad" primero (selector bestvideo/best)
    tracks.append(
        {
            "value": "__best__",
            "label": "Mejor calidad disponible (bestvideo)",
        }
    )

    def sort_key(item):
        key, bucket = item      # Separa la clave del valor
        h, ext = key            # Ahora sí, separa la altura del formato
        try:
            h_num = int(h) if h is not None else 0
        except (ValueError, TypeError):
            h_num = 0
        return (-h_num, ext)

    for (height, ext), bucket in sorted(buckets.items(), key=sort_key):
        ids = bucket["ids"]
        if not ids:
            continue

        # Selector que agrupa todos los format_id de esa resolución
        value = ",".join(ids) if len(ids) > 1 else ids[0]

        if height:
            label = f"{height}p ({ext.upper()})"
        else:
            label = f"Desconocida ({ext.upper()})"

        if bucket["note"]:
            label += f" · {bucket['note']}"

        tracks.append({"value": value, "label": label})

    return tracks


def extract_subtitle_langs(info: Dict[str, Any], automatic: bool = False) -> List[str]:
    if automatic:
        return sorted(info.get("automatic_captions", {}).keys())
    return sorted(info.get("subtitles", {}).keys())


# ============================================================
# Subtítulos
# ============================================================

def safe_upload_path(filename: str) -> Path:
    if not filename:
        raise ValueError("No se indicó archivo SRT")

    base = UPLOAD_DIR.resolve()
    candidate = (UPLOAD_DIR / filename).resolve()

    try:
        candidate.relative_to(base)
    except ValueError:
        raise ValueError("Ruta de SRT inválida")

    if not candidate.is_file():
        raise ValueError("El archivo SRT no existe")

    return candidate


def find_srt_for_lang(directory: Path, lang: str, allow_any: bool = False) -> Optional[Path]:
    patterns = [
        f"*.{lang}*.srt",
        f"*.{lang.split('-')[0]}*.srt",
    ]

    for pattern in patterns:
        files = list(directory.glob(pattern))
        if files:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return files[0]

    if allow_any:
        files = list(directory.glob("*.srt"))
        if len(files) == 1:
            return files[0]

    return None


def download_subtitle_files(
    sub_type: str,
    sub_lang: str,
    url: str,
    job_dir: Path,
) -> List[tuple]:
    subs_dir = job_dir / "subs"
    subs_dir.mkdir(exist_ok=True)

    if sub_lang == "__all__":
        info = get_ytdlp_info(url)

        if sub_type == "automatic":
            langs = sorted(info.get("automatic_captions", {}).keys())
        else:
            langs = sorted(info.get("subtitles", {}).keys())

        if not langs:
            return []
    else:
        langs = [sub_lang]

    langs_str = ",".join(langs)

    command = [
        get_binary_path("yt-dlp"),
        "--newline",
        "--skip-download",
        "--convert-subs",
        "srt",
        "--sub-langs",
        langs_str,
        "-P",
        str(subs_dir),
        "--no-playlist",
    ] + get_cookies_args()

    if sub_type == "automatic":
        command.append("--write-auto-subs")
    else:
        command.append("--write-subs")

    command.append(url)

    run_command(command)

    result = []
    allow_any = sub_lang != "__all__"

    for lang in langs:
        srt = find_srt_for_lang(subs_dir, lang, allow_any=allow_any)
        if srt:
            result.append((lang, srt))
        else:
            add_log(f"No se encontró subtítulo para: {lang}", "warning")

    return result


def get_subtitle_tracks(req: DownloadRequest, job_dir: Path) -> List[tuple]:
    if req.subtitle_type == "none":
        return []

    if req.subtitle_type == "external" and req.external_sub_source == "file":
        src = safe_upload_path(req.uploaded_srt)

        original_name = Path(req.uploaded_srt_original_name or src.name).name
        dst = job_dir / original_name

        if dst.exists():
            dst = job_dir / f"{uuid.uuid4().hex}_{original_name}"

        shutil.copy(src, dst)

        lang = Path(original_name).stem or "external"
        return [(lang, dst)]

    url = req.video_url

    if req.subtitle_type == "external" and req.external_sub_source == "url":
        url = req.subtitle_url or req.video_url

    if not req.subtitle_lang:
        return []

    return download_subtitle_files(
        sub_type=req.subtitle_type,
        sub_lang=req.subtitle_lang,
        url=url,
        job_dir=job_dir,
    )


# ============================================================
# Descarga de video/audio
# ============================================================

def download_ytdlp_file(
    url: str,
    format_selector: str,
    output_template: str,
    job_dir: Path,
    prefix: str,
    stage: Optional[str] = None,
) -> Path:
    command = [
        get_binary_path("yt-dlp"),
        "--newline",
        "--no-playlist",
    ] + get_cookies_args() + [
        "-f",
        format_selector,
        "-o",
        output_template,
        url,
    ]

    if stage:
        send_event("stage", {"stage": stage})

    run_command(command)

    files = list(job_dir.glob(f"{prefix}.*"))
    files = [
        f
        for f in files
        if f.suffix.lower() not in [".part", ".ytdl"]
    ]

    if not files:
        raise RuntimeError("No se encontró el archivo descargado")

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


# ============================================================
# FFmpeg
# ============================================================

def select_font(title: str) -> str:
    default_font = "Gandhi Sans"

    keywords = [
        "Zenless Zone Zero",
        "ゼンレスゾーンゼロ",
        "絕區零",
        "Zenless",
        "Honkai Star Rail",
        "崩壊：スターレイル",
        "Honkai: Star Rail",
        "Genshin Impact",
        "原神",
        "Genshin",
    ]

    if any(keyword.lower() in title.lower() for keyword in keywords):
        return default_font

    return default_font


def lang_to_iso639_2(lang: str) -> str:
    mapping = {
        "es": "spa",
        "en": "eng",
        "ja": "jpn",
        "pt": "por",
        "fr": "fra",
        "de": "deu",
        "it": "ita",
        "ko": "kor",
        "ru": "rus",
        "zh": "zho",
    }

    base = lang.split("-")[0].lower()
    return mapping.get(base, base)


def combine_files(
    video_path: Path,
    audio_path: Path,
    subtitle_tracks: List[tuple],
    subtitle_options: Dict[str, bool],
    font_name: str,
    final_video_name: Path,
) -> None:
    command = [
        get_binary_path("ffmpeg"),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
    ]

    embedded_subtitles = []
    next_input = 2

    if subtitle_tracks and subtitle_options.get("embed"):
        for lang, path in subtitle_tracks:
            command.extend(["-i", str(path)])
            embedded_subtitles.append((next_input, lang))
            next_input += 1

    video_params = [
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-b:v",
        "10000k",
        "-maxrate",
        "15000k",
        "-bufsize",
        "20000k",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
    ]

    command.extend(video_params)

    vf = "scale=-2:1080"

    if subtitle_tracks and subtitle_options.get("burn"):
        burn_lang, burn_path = subtitle_tracks[0]

        escaped_path = (
            str(burn_path)
            .replace("\\", "/")
            .replace(":", "\\:")
        )

        subtitle_filter = f"subtitles='{escaped_path}'"

        if font_name:
            subtitle_filter += f":force_style='Fontname={font_name},FontSize=20'"

        vf += "," + subtitle_filter

    command.extend(["-vf", vf])

    command.extend([
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ])

    command.extend([
        "-c:a",
        "aac",
        "-b:a",
        "192k",
    ])

    if embedded_subtitles:
        command.extend(["-c:s", "mov_text"])

        for index, (input_index, lang) in enumerate(embedded_subtitles):
            command.extend([
                "-map",
                f"{input_index}:s:0",
                f"-metadata:s:s:{index}",
                f"language={lang_to_iso639_2(lang)}",
            ])

    command.extend([
        "-movflags",
        "+faststart",
        str(final_video_name),
    ])

    run_command(command)


# ============================================================
# Trabajo de descarga
# ============================================================

def download_worker(req: DownloadRequest) -> None:
    job_dir: Optional[Path] = None

    try:
        deps = get_dependency_status()
        if not deps["ok"]:
            raise RuntimeError(
                f"Faltan dependencias: {', '.join(deps['missing'])}"
            )

        check_cancelled()

        job_name = (
            f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        job_dir = TEMP_DIR / job_name
        job_dir.mkdir(parents=True, exist_ok=False)

        title = req.video_title

        if not title:
            add_log("Obteniendo título del video...", "info")
            info = get_ytdlp_info(req.video_url)
            title = info.get("title", "video_sin_titulo")

        final_dir = Path(req.output_dir).expanduser().resolve()
        final_dir.mkdir(parents=True, exist_ok=True)

        final_path = final_dir / f"{sanitize_filename(title)}.mp4"

        add_log(f"Título del video: {title}", "info")
        add_log(f"Salida final: {final_path}", "info")

        check_cancelled()

        add_log("--- Descargando video principal ---", "info")

        if req.video_format_id and req.video_format_id != "__best__":
            # El frontend envía uno o varios format_id explícitos
            # (p. ej. "137" o "137,301") agrupados por resolución.
            video_selector = req.video_format_id.replace(" ", "")
        else:
            video_selector = "bestvideo[ext=mp4]/bestvideo"

        video_path = download_ytdlp_file(
            url=req.video_url,
            format_selector=video_selector,
            output_template=str(job_dir / "temp_video.%(ext)s"),
            job_dir=job_dir,
            prefix="temp_video",
            stage="video",
        )

        check_cancelled()

        audio_url = req.video_url
        if req.audio_source == "external":
            audio_url = req.audio_url

        add_log("--- Descargando audio ---", "info")

        audio_path = download_ytdlp_file(
            url=audio_url,
            format_selector=req.audio_format_id,
            output_template=str(job_dir / "temp_audio.%(ext)s"),
            job_dir=job_dir,
            prefix="temp_audio",
            stage="audio",
        )

        check_cancelled()

        subtitle_tracks = []

        if req.subtitle_type != "none":
            add_log("--- Procesando subtítulos ---", "info")

            subtitle_tracks = get_subtitle_tracks(req, job_dir)

            if not subtitle_tracks:
                add_log("No se obtuvieron subtítulos", "warning")
            else:
                add_log(
                    f"Subtítulos obtenidos: {len(subtitle_tracks)} pista(s)",
                    "success",
                )

        check_cancelled()

        subtitle_options = {
            "burn": req.burn_subtitles and bool(subtitle_tracks),
            "embed": req.embed_subtitles and bool(subtitle_tracks),
        }

        font_name = select_font(title)

        add_log("--- Combinando y convirtiendo con FFmpeg ---", "info")

        send_event("stage", {"stage": "merge"})
        # Barra indeterminada durante el merge de FFmpeg
        send_event(
            "progress",
            {
                "percent": None,
                "size": None,
                "speed": None,
                "eta": "Procesando FFmpeg...",
                "raw": "ffmpeg merge",
            },
        )

        combine_files(
            video_path=video_path,
            audio_path=audio_path,
            subtitle_tracks=subtitle_tracks,
            subtitle_options=subtitle_options,
            font_name=font_name,
            final_video_name=final_path,
        )

        if req.keep_srt and subtitle_tracks:
            for lang, path in subtitle_tracks:
                if (
                    req.subtitle_type == "external"
                    and req.external_sub_source == "file"
                    and req.uploaded_srt_original_name
                ):
                    dest = final_dir / Path(req.uploaded_srt_original_name).name
                else:
                    dest = final_dir / f"{final_path.stem}.{lang}.srt"

                shutil.copy2(path, dest)

            add_log("Subtítulos .srt conservados", "success")

        save_config(str(final_dir))

        send_event("progress_done", {"ok": True})

        add_log(
            f"¡PROCESO COMPLETADO! Video guardado en: {final_path}",
            "success",
        )

    except JobCancelled:
        send_event("progress_done", {"ok": False, "reason": "cancelled"})
        add_log("Proceso cancelado por el usuario", "warning")

    except Exception as e:
        send_event("progress_done", {"ok": False, "reason": str(e)})
        add_log(f"ERROR DURANTE EL PROCESO: {e}", "error")

    finally:
        if job_dir and job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)

        with state.lock:
            state.busy = False

        notify_status()


# ============================================================
# Validación
# ============================================================

def validate_download_request(req: DownloadRequest) -> None:
    if not is_youtube_url(req.video_url):
        raise HTTPException(400, "La URL del video principal no es válida")

    if not req.output_dir.strip():
        raise HTTPException(400, "El directorio de salida no puede estar vacío")

    if not req.audio_format_id:
        raise HTTPException(400, "Selecciona una pista de audio válida")

    if req.audio_source == "external":
        if not req.audio_url or not is_youtube_url(req.audio_url):
            raise HTTPException(400, "La URL del audio externo no es válida")

    if req.subtitle_type != "none":
        if not req.burn_subtitles and not req.embed_subtitles:
            req.burn_subtitles = True

        if req.subtitle_type in ["internal", "automatic"]:
            if not req.subtitle_lang:
                raise HTTPException(400, "Selecciona un idioma de subtítulos")

        elif req.subtitle_type == "external":
            if req.external_sub_source == "url":
                if not req.subtitle_url or not is_youtube_url(req.subtitle_url):
                    raise HTTPException(
                        400,
                        "La URL de subtítulos externos no es válida",
                    )

                if not req.subtitle_lang:
                    raise HTTPException(
                        400,
                        "Selecciona un idioma de subtítulos externos",
                    )

            elif req.external_sub_source == "file":
                if not req.uploaded_srt:
                    raise HTTPException(400, "No se seleccionó un archivo SRT")

                try:
                    safe_upload_path(req.uploaded_srt)
                except ValueError as e:
                    raise HTTPException(400, str(e))

            else:
                raise HTTPException(
                    400,
                    "Fuente de subtítulos externos no válida",
                )


# ============================================================
# Endpoints de estado, logs y configuración
# ============================================================

@app.on_event("startup")
async def capture_event_loop() -> None:
    """Guarda el event loop de uvicorn para poder emitir eventos
    desde los hilos de trabajo (yt-dlp / ffmpeg)."""
    ws_manager.loop = asyncio.get_running_loop()


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await ws_manager.connect(websocket)

    try:
        # 1) Historial reciente para no perder contexto al conectar
        with LOG_LOCK:
            history = list(LOGS[-500:])
            cursor = LOG_ID

        await websocket.send_json(
            {
                "type": "history",
                "data": {"logs": history, "next": cursor},
            }
        )

        # 2) Estado actual + progreso inicial
        await websocket.send_json(
            {"type": "status", "data": get_busy_snapshot()}
        )

        # 3) Mantener la conexión viva y responder pings
        while True:
            try:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json({"type": "pong", "data": {}})
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        # Cliente caído de forma abrupta: cerramos sin propagar el error
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        await ws_manager.disconnect(websocket)


@app.get("/api/health")
def health():
    deps = get_dependency_status()
    return {
        "ok": deps["ok"],
        "dependencies": deps,
    }


@app.get("/api/status")
def status():
    return {
        "busy": state.busy,
    }


@app.get("/api/logs")
def logs(after: int = 0):
    with LOG_LOCK:
        items = [log for log in LOGS if log["id"] > after]
        return {
            "logs": items[-500:],
            "next": LOG_ID,
        }


@app.get("/api/config")
def get_config():
    return {
        "last_output_dir": state.last_output_dir,
    }


@app.post("/api/config")
def set_config(payload: ConfigPayload):
    save_config(payload.output_dir)
    return {
        "ok": True,
        "last_output_dir": state.last_output_dir,
    }


# ============================================================
# Explorador de carpetas del servidor
# ============================================================

@app.get("/api/fs")
def list_fs(path: Optional[str] = None):
    try:
        p = Path(path).expanduser().resolve() if path else Path.home()
    except Exception:
        p = Path.home()

    if not p.exists() or not p.is_dir():
        p = Path.home()

    dirs = []

    try:
        for item in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir():
                dirs.append(
                    {
                        "name": item.name,
                        "path": str(item),
                    }
                )
    except PermissionError:
        pass

    return {
        "current": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "home": str(Path.home()),
        "dirs": dirs,
    }


# ============================================================
# Subida de SRT
# ============================================================

@app.post("/api/upload-srt")
async def upload_srt(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".srt"):
        raise HTTPException(400, "Solo se permiten archivos .srt")

    safe_base = (
        f"{uuid.uuid4().hex}_"
        f"{sanitize_filename(Path(file.filename).stem)}"
        f".srt"
    )

    dest = UPLOAD_DIR / safe_base

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    return {
        "ok": True,
        "filename": safe_base,
        "original_name": Path(file.filename).name,
    }

# ============================================================
# Cookies
# ============================================================

@app.get("/api/cookies")
def get_cookies_config():
    return {
        "mode": state.cookies_mode,
        "browser": state.cookies_browser,
        "profile": state.cookies_profile,
        "file_exists": COOKIES_FILE.exists(),
        "args": get_cookies_args(),
    }


@app.post("/api/cookies")
def set_cookies_config(payload: CookiesPayload):
    state.cookies_mode = payload.mode
    state.cookies_browser = (payload.browser or "firefox").strip() or "firefox"
    state.cookies_profile = (payload.profile or "").strip() or None

    save_config()

    add_log(
        f"Configuración de cookies actualizada: modo={state.cookies_mode}",
        "info",
    )

    return {
        "ok": True,
        "args": get_cookies_args(),
    }


@app.post("/api/upload-cookies")
async def upload_cookies(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(400, "Sube un archivo cookies.txt (.txt)")

    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    if (
        "# Netscape HTTP Cookie File" not in text
        and "# HTTP Cookie File" not in text
    ):
        raise HTTPException(
            400,
            "El archivo no parece un cookies.txt válido (formato Netscape)",
        )

    COOKIES_FILE.write_bytes(content)

    state.cookies_mode = "file"
    save_config()

    add_log("cookies.txt subido y activado", "success")

    return {
        "ok": True,
        "mode": "file",
    }

# ============================================================
# Obtener información de YouTube
# ============================================================

@app.post("/api/info")
def info(req: InfoRequest):
    if state.busy:
        raise HTTPException(
            409,
            "Ya hay una operación en curso. Espera a que termine.",
        )

    if not is_youtube_url(req.url):
        raise HTTPException(400, "URL de YouTube no válida")

    deps = get_dependency_status()
    if not deps["ok"]:
        raise HTTPException(
            500,
            f"Faltan dependencias: {', '.join(deps['missing'])}",
        )

    try:
        add_log(f"Obteniendo información de: {req.url}", "info")
        info_data = get_ytdlp_info(req.url)
    except Exception as e:
        raise HTTPException(502, f"No se pudo obtener información: {e}")

    response: Dict[str, Any] = {
        "ok": True,
        "title": info_data.get("title"),
        "id": info_data.get("id"),
    }

    if req.kind in ["main", "audio"]:
        response["audio_tracks"] = extract_audio_tracks(info_data)

    if req.kind == "main":
        response["video_tracks"] = extract_video_tracks(info_data)

    if req.kind in ["main", "subs"]:
        response["subtitles"] = extract_subtitle_langs(info_data, automatic=False)
        # Idiomas REALES de subtítulos automáticos leídos del JSON de yt-dlp
        # (clave "automatic_captions"), ya no se usa una lista hardcoded.
        response["automatic_captions"] = extract_subtitle_langs(
            info_data,
            automatic=True,
        )

    return response


# ============================================================
# Descarga
# ============================================================

@app.post("/api/download")
def start_download(req: DownloadRequest):
    validate_download_request(req)

    deps = get_dependency_status(force=True)
    if not deps["ok"]:
        raise HTTPException(
            500,
            f"Faltan dependencias: {', '.join(deps['missing'])}",
        )

    with state.lock:
        if state.busy:
            raise HTTPException(
                409,
                "Ya hay una operación en curso",
            )

        state.busy = True
        state.cancel_event.clear()

    notify_status()

    thread = threading.Thread(
        target=download_worker,
        args=(req,),
        daemon=True,
    )
    thread.start()

    return {
        "ok": True,
        "message": "Descarga iniciada",
    }


@app.post("/api/stop")
def stop():
    state.cancel_event.set()

    with state.lock:
        process = state.current_process

    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()

        add_log("Proceso interrumpido por el usuario", "warning")
    else:
        add_log("No hay proceso activo para interrumpir", "info")

    notify_status()

    return {
        "ok": True,
    }


# ============================================================
# Frontend estático
# ============================================================

if STATIC_DIR.exists():
    app.mount(
        "/",
        StaticFiles(directory=STATIC_DIR, html=True),
        name="static",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
