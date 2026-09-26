import os
import re
import base64
import json
import hmac
import hashlib
import secrets
import shutil
import zipfile
import subprocess
import tempfile
import time
import tarfile
import platform
import urllib.request
import sys

# Streamlit Cloud safety net: if requirements.txt was not applied to the
# current build, install the downloader package before importing it.
def _ensure_yt_dlp():
    try:
        import yt_dlp as _yt_dlp
        return _yt_dlp
    except ModuleNotFoundError:
        import subprocess as _sp
        _sp.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-cache-dir", "yt-dlp==2026.8.19"],
            check=True,
        )
        import yt_dlp as _yt_dlp
        return _yt_dlp

yt_dlp = _ensure_yt_dlp()

from pathlib import Path
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
try:
    import curl_cffi
except Exception:
    curl_cffi = None
import cv2
from pytubefix import YouTube

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


# ============================================================
# CONFIG
# ============================================================

# Scalable processing: parts are processed/uploaded one at a time so 20+ Shorts
# do not remain in memory simultaneously.
MAX_PARTS = 200
CLEANUP_AFTER_UPLOAD = True

st.set_page_config(
    page_title="Multi-Channel Shorts Automator",
    page_icon="🎬",
    layout="centered"
)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

YOUTUBE_SCOPE = [
    # Needed for uploading Shorts.
    "https://www.googleapis.com/auth/youtube.upload",
    # Needed for channels.list(mine=True) so the app can verify which
    # YouTube channel is connected to this OAuth account.
    "https://www.googleapis.com/auth/youtube.readonly",
]


# ============================================================
# PAGE
# ============================================================

st.title("🎬 Multi-Channel Shorts Automator")
st.caption("Upload or YouTube Link • pytubefix + yt-dlp fallback • Auto Shorts • Scheduling • YouTube Upload")

# Compact UI
st.markdown("""
<style>
    .block-container { padding-top: 2.2rem; padding-bottom: 1rem; max-width: 900px; }
    [data-testid="stVerticalBlock"] { gap: 0.45rem; }
    [data-testid="stExpander"] { margin-bottom: 0.3rem; }
    [data-testid="stExpanderDetails"] { padding-top: 0.3rem; padding-bottom: 0.3rem; }
    hr { margin: 0.5rem 0; }
    h1 { margin-top: 0.2rem; margin-bottom: 0.3rem; font-size: 2.05rem; line-height: 1.15; }
    h2, h3 { margin-top: 0.3rem; margin-bottom: 0.2rem; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# HELPERS
# ============================================================



def create_face_follow_video(
    input_path,
    start_time,
    duration,
    output_path,
    output_width,
    output_height,
    zoom=1.25,
):
    """Create a silent 9:16 video that smoothly follows the most prominent face."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError("Source video face tracking ke liye open nahi hua.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if src_w <= 0 or src_h <= 0:
        cap.release()
        raise RuntimeError("Source frame size read nahi hua.")

    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(start_time * fps)))

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    if cascade.empty():
        cap.release()
        raise RuntimeError("OpenCV face detector load nahi hua.")

    target_ratio = output_width / output_height
    crop_w = min(src_w, int(src_h * target_ratio))
    crop_h = min(src_h, int(crop_w / target_ratio))

    zoom = max(1.0, float(zoom))
    crop_w = max(64, min(src_w, int(crop_w / zoom)))
    crop_h = max(64, min(src_h, int(crop_w / target_ratio)))

    writer = cv2.VideoWriter(
        output_path,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (output_width, output_height)
    )
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Face-follow temporary video create nahi hua.")

    max_frames = max(1, int(duration * fps))
    detect_every = max(1, int(fps / 5))
    cx, cy = src_w / 2.0, src_h / 2.0
    written = 0

    try:
        while written < max_frames:
            ok, frame = cap.read()
            if not ok:
                break

            if written % detect_every == 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(40, 40)
                )
                if len(faces):
                    x, y, w, h = max(
                        faces,
                        key=lambda f: int(f[2]) * int(f[3])
                    )
                    target_cx = x + w / 2.0
                    target_cy = y + h / 2.0
                    # Smooth movement so the camera doesn't jump.
                    cx = cx * 0.78 + target_cx * 0.22
                    cy = cy * 0.78 + target_cy * 0.22

            x1 = int(cx - crop_w / 2)
            y1 = int(cy - crop_h / 2)
            x1 = max(0, min(x1, src_w - crop_w))
            y1 = max(0, min(y1, src_h - crop_h))

            crop = frame[y1:y1 + crop_h, x1:x1 + crop_w]
            if crop.size == 0:
                crop = frame

            crop = cv2.resize(
                crop,
                (output_width, output_height),
                interpolation=cv2.INTER_AREA
            )
            writer.write(crop)
            written += 1
    finally:
        cap.release()
        writer.release()

    if written == 0 or not Path(output_path).exists():
        cleanup_temp_file(output_path)
        raise RuntimeError("Face-follow video me frames generate nahi hue.")

    return output_path

def cleanup_temp_file(path):
    """Safely remove a temporary generated part after upload/scheduling."""
    try:
        p = Path(path)
        if p.exists() and p.is_file():
            p.unlink()
    except Exception:
        pass

def create_title_overlay_png(text, output_path, width=640, height=90):
    """Render the title with Pillow instead of FFmpeg drawtext.

    Some Streamlit/imageio-ffmpeg static builds do not include the drawtext
    filter even when freetype is compiled in. A PNG overlay works with the
    standard FFmpeg overlay filter and is therefore much more portable.
    """
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Prefer a common Linux font on Streamlit Cloud; fall back to PIL's font.
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    font = None
    font_size = max(18, int(width / 28))
    for font_path in font_candidates:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    # Semi-transparent rounded black title box.
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=14,
        fill=(0, 0, 0, 115),
    )

    # Fit the text to the box.
    text = str(text).strip()[:45]
    while len(text) > 1:
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        if bbox[2] - bbox[0] <= width - 30:
            break
        text = text[:-1]

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) / 2
    y = (height - th) / 2 - bbox[1]

    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 180),
    )

    image.save(output_path, "PNG")


def clean_filename(text):
    text = str(text).strip()
    text = re.sub(r'[\\/:*?"<>|]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip(" .")
    return text or "Kids_Fun_Video"


def clean_youtube_title(text):
    text = re.sub(r'[\r\n\t]', ' ', str(text).strip())
    text = re.sub(r'\s+', ' ', text)
    return text[:95]


def get_video_duration(video_path):
    result = subprocess.run(
        [FFMPEG, "-i", video_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        result.stderr
    )

    if not match:
        raise RuntimeError("Video duration read nahi ho paayi.")

    h = int(match.group(1))
    m = int(match.group(2))
    s = float(match.group(3))

    return h * 3600 + m * 60 + s


def run_ffmpeg(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])

    return result


def make_thumbnail(video_path, output_path):
    try:
        run_ffmpeg([
            FFMPEG, "-y",
            "-ss", "2",
            "-i", video_path,
            "-frames:v", "1",
            "-vf", "scale=720:-1",
            output_path
        ])
        return os.path.exists(output_path)
    except Exception:
        return False


def make_title(topic, part_number, add_shorts=True):
    title = (
        f"{clean_youtube_title(topic)} | "
        f"Fun Kids Video | Part {part_number}"
    )

    if add_shorts:
        title += " #Shorts"

    return clean_youtube_title(title)



def is_valid_youtube_url(url):
    """Basic validation for normal YouTube watch/shorts/youtu.be URLs."""
    url = str(url or "").strip()
    return bool(re.match(
        r"^https?://(www\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)",
        url,
        re.IGNORECASE
    ))


def _youtube_url_ok(url):
    url = str(url or "").strip()
    return bool(re.match(
        r"^https?://(www\.)?(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)",
        url,
        re.IGNORECASE
    ))


def _youtube_cookie_path(uploaded_cookie_file=None):
    """Persist an uploaded cookies.txt only for the current app runtime.
    The file is never displayed or logged.
    """
    if uploaded_cookie_file is None:
        return ""

    try:
        data = uploaded_cookie_file.getvalue()
        if not data:
            return ""
        cookie_path = os.path.join(
            tempfile.gettempdir(),
            "yt_cookies_" + hashlib.sha256(data).hexdigest()[:20] + ".txt"
        )
        if not os.path.exists(cookie_path):
            with open(cookie_path, "wb") as f:
                f.write(data)
        return cookie_path
    except Exception:
        return ""


def _cookie_option(options, cookies_path):
    if cookies_path:
        options["cookiefile"] = cookies_path
    return options


def _pytubefix_info(url):
    yt = YouTube(url)
    return {
        "title": yt.title or "YouTube Video",
        "duration": float(yt.length or 0),
        "webpage_url": url,
    }


def _pytubefix_download(url, output_path):
    yt = YouTube(url)

    progressive = (
        yt.streams
        .filter(progressive=True, file_extension="mp4")
        .order_by("resolution")
        .desc()
        .first()
    )

    if progressive is None:
        progressive = (
            yt.streams
            .filter(file_extension="mp4")
            .order_by("resolution")
            .desc()
            .first()
        )

    if progressive is None:
        raise RuntimeError("pytubefix ko downloadable MP4 stream nahi mila.")

    tmp_dir = os.path.dirname(output_path) or "."
    downloaded = progressive.download(
        output_path=tmp_dir,
        filename=os.path.basename(output_path)
    )

    if not os.path.exists(downloaded):
        raise RuntimeError("pytubefix download file nahi bana saka.")

    if downloaded != output_path:
        cleanup_temp_file(output_path)
        os.replace(downloaded, output_path)

    return output_path



_BGUTIL_PROCESS = None
_BGUTIL_VERSION = "2.0.0"
_BGUTIL_URL = "http://127.0.0.1:4416"


def _start_local_bgutil_provider():
    """Download/cache and start the official bgutil POT server locally.

    The server is bound to loopback only. It is started once per Streamlit
    process and reused by yt-dlp. Version 2.0.0 matches the current plugin
    release installed from PyPI.
    """
    global _BGUTIL_PROCESS

    # Respect an explicitly configured external provider first.
    configured = os.environ.get("BGUTIL_BASE_URL", "").strip()
    if configured:
        return configured
    try:
        configured = str(st.secrets.get("BGUTIL_BASE_URL", "")).strip()
    except Exception:
        configured = ""
    if configured:
        return configured

    # Already healthy?
    try:
        with urllib.request.urlopen(_BGUTIL_URL + "/ping", timeout=2) as resp:
            if resp.status == 200:
                return _BGUTIL_URL
    except Exception:
        pass

    deno = _ensure_deno_runtime()
    if not deno:
        return ""

    cache_root = Path.home() / ".cache" / "yt_shorts_tool" / "bgutil" / _BGUTIL_VERSION
    repo_dir = cache_root / f"bgutil-ytdlp-pot-provider-{_BGUTIL_VERSION}"
    server_dir = repo_dir / "server"
    src_main = server_dir / "src" / "main.ts"

    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return ""

    # Download the matching official release source if needed.
    if not src_main.exists():
        archive = cache_root / f"bgutil-{_BGUTIL_VERSION}.tar.gz"
        archive_url = (
            f"https://github.com/Brainicism/bgutil-ytdlp-pot-provider/"
            f"archive/refs/tags/{_BGUTIL_VERSION}.tar.gz"
        )
        try:
            urllib.request.urlretrieve(archive_url, str(archive))
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(cache_root, filter="data")
            try:
                archive.unlink()
            except Exception:
                pass
        except Exception:
            return ""

    if not src_main.exists():
        return ""

    # Install the server's Deno/npm dependencies once.
    node_modules = server_dir / "node_modules"
    install_marker = server_dir / ".deno_install_ok"
    if not install_marker.exists():
        try:
            proc = subprocess.run(
                [
                    deno,
                    "install",
                    "--allow-scripts=npm:canvas",
                    "--frozen",
                ],
                cwd=str(server_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                return ""
            install_marker.write_text("ok", encoding="utf-8")
        except Exception:
            return ""

    # If a previous process is still alive, reuse it.
    if _BGUTIL_PROCESS is not None and _BGUTIL_PROCESS.poll() is None:
        return _BGUTIL_URL

    try:
        _BGUTIL_PROCESS = subprocess.Popen(
            [
                deno,
                "run",
                "--allow-env",
                "--allow-net",
                "--allow-ffi=.",
                "--allow-read=.",
                "../src/main.ts",
                "--host",
                "127.0.0.1",
                "--port",
                "4416",
            ],
            cwd=str(node_modules),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _BGUTIL_PROCESS = None
        return ""

    # Wait briefly for the local provider to become ready.
    for _ in range(30):
        if _BGUTIL_PROCESS.poll() is not None:
            _BGUTIL_PROCESS = None
            return ""
        try:
            with urllib.request.urlopen(_BGUTIL_URL + "/ping", timeout=1) as resp:
                if resp.status == 200:
                    return _BGUTIL_URL
        except Exception:
            time.sleep(0.5)

    return ""


def _get_bgutil_base_url():
    value = os.environ.get("BGUTIL_BASE_URL", "").strip()
    if value:
        return value
    try:
        value = str(st.secrets.get("BGUTIL_BASE_URL", "")).strip()
        if value:
            return value
    except Exception:
        pass

    # V32 automatically starts the matching local PO-token provider.
    return _start_local_bgutil_provider()


def _yt_format_selector():
    # Progressive-first: avoid FFmpeg whenever YouTube exposes a combined
    # video+audio stream. If no combined stream exists, yt-dlp can fall back
    # to a merge selector.
    return "best[ext=mp4]/best/bv*+ba/b"


def _ensure_deno_runtime():
    """Ensure Deno >= 2.x is available for yt-dlp's YouTube JS challenge solver.

    Streamlit Cloud does not necessarily have Deno preinstalled. Installing
    yt-dlp-ejs alone is not enough: yt-dlp explicitly requires a supported JS
    runtime for YouTube challenge solving. V31 bootstraps the official Deno
    Linux binary into a user-writable cache directory when needed.
    """
    existing = shutil.which("deno")
    if existing:
        return existing

    cache_dir = Path.home() / ".cache" / "yt_shorts_tool" / "deno"
    cache_dir.mkdir(parents=True, exist_ok=True)
    deno_path = cache_dir / "deno"

    if deno_path.exists():
        try:
            deno_path.chmod(0o755)
            check = subprocess.run(
                [str(deno_path), "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            if check.returncode == 0:
                return str(deno_path)
        except Exception:
            pass

    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        asset = "deno-x86_64-unknown-linux-gnu.zip"
    elif machine in {"aarch64", "arm64"}:
        asset = "deno-aarch64-unknown-linux-gnu.zip"
    else:
        return ""

    url = f"https://github.com/denoland/deno/releases/latest/download/{asset}"
    zip_path = cache_dir / asset

    try:
        urllib.request.urlretrieve(url, str(zip_path))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            deno_member = next(
                (n for n in names if Path(n).name == "deno"),
                None,
            )
            if not deno_member:
                return ""
            with zf.open(deno_member) as src_f, open(deno_path, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)

        deno_path.chmod(0o755)
        try:
            zip_path.unlink()
        except Exception:
            pass

        check = subprocess.run(
            [str(deno_path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if check.returncode == 0:
            return str(deno_path)
    except Exception:
        try:
            zip_path.unlink()
        except Exception:
            pass

    return ""


def _yt_runtime_options():
    # Deno is the recommended runtime for current yt-dlp YouTube extraction.
    deno = _ensure_deno_runtime()
    if deno:
        return {"deno": {"path": deno}}

    node = shutil.which("node")
    if node:
        return {"node": {"path": node}}

    return {}


def _ffmpeg_executable():
    # imageio-ffmpeg bundles a compatible ffmpeg binary.
    # Use it explicitly so yt-dlp can merge adaptive video + audio
    # even when the ffmpeg executable is not on Streamlit Cloud PATH.
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg") or ""


def _ytdlp_base_options(output_path):
    options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "format": _yt_format_selector(),
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_executable(),
        "js_runtimes": _yt_runtime_options(),
        "remote_components": ["ejs:github"],
        "outtmpl": output_path,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 30,
    }

    bgutil_base_url = _get_bgutil_base_url()

    # Only add the provider argument when a real HTTP provider is configured.
    if bgutil_base_url:
        options["extractor_args"] = {
            "youtube": {
                "player_client": ["mweb"],
            },
            "youtubepot-bgutilhttp": {
                "base_url": bgutil_base_url
            },
        }
    return options


def _ytdlp_info_with_clients(url, cookies_path=""):
    """Read metadata with genuinely different YouTube clients."""
    errors = []
    for label, extractor_args in _youtube_client_attempts(include_bgutil=True):
        try:
            options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "js_runtimes": _yt_runtime_options(),
                "remote_components": ["ejs:github"],
                "socket_timeout": 30,
            }
            if extractor_args:
                options["extractor_args"] = extractor_args
            _cookie_option(options, cookies_path)

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)

            if info.get("_type") == "playlist":
                raise ValueError("Playlist link nahi, ek single YouTube video link do.")

            return {
                "title": info.get("title") or "YouTube Video",
                "duration": float(info.get("duration") or 0),
                "webpage_url": info.get("webpage_url") or url,
            }
        except Exception as e:
            errors.append(f"{label}: {e}")

    raise RuntimeError("yt-dlp metadata attempts failed:\n" + "\n".join(errors))




def _youtube_format_diagnostic(url, cookies_path=""):
    """Inspect formats for each real client without downloading."""
    results = []
    failures = []

    for label, extractor_args in _youtube_client_attempts(include_bgutil=True):
        try:
            options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "js_runtimes": _yt_runtime_options(),
                "remote_components": ["ejs:github"],
                "socket_timeout": 30,
            }
            if extractor_args:
                options["extractor_args"] = extractor_args
            _cookie_option(options, cookies_path)

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)

            formats = info.get("formats") or []
            usable = []
            for f in formats:
                protocol = str(f.get("protocol") or "")
                vcodec = str(f.get("vcodec") or "")
                acodec = str(f.get("acodec") or "")
                if not vcodec or vcodec == "none":
                    continue
                usable.append({
                    "id": str(f.get("format_id") or ""),
                    "ext": str(f.get("ext") or ""),
                    "resolution": str(f.get("resolution") or (
                        f"{f.get('width')}x{f.get('height')}"
                        if f.get("width") and f.get("height") else ""
                    )),
                    "fps": f.get("fps"),
                    "protocol": protocol,
                    "video": vcodec,
                    "audio": acodec,
                    "filesize_mb": round((f.get("filesize") or f.get("filesize_approx") or 0) / 1024 / 1024, 1),
                    "has_audio": bool(acodec and acodec != "none"),
                })

            usable.sort(key=lambda x: (
                int(re.search(r"(\d+)", x["resolution"]).group(1))
                if re.search(r"(\d+)", x["resolution"]) else 0,
                1 if x["has_audio"] else 0,
                x["fps"] or 0,
            ), reverse=True)

            results.append({
                "client": label,
                "title": info.get("title") or "",
                "format_count": len(formats),
                "video_format_count": len(usable),
                "formats": usable[:15],
            })
        except Exception as e:
            failures.append(f"{label}: {e}")

    return results, failures




def _pick_progressive_format(info):
    """Pick a directly downloadable format containing both video and audio.

    This intentionally avoids video-only + audio-only merging when possible.
    The diagnostic shown by the app proves YouTube is exposing many formats;
    selecting a concrete progressive format is more reliable on constrained
    Streamlit Cloud deployments.
    """
    formats = info.get("formats") or []
    candidates = []
    for f in formats:
        try:
            vcodec = str(f.get("vcodec") or "none")
            acodec = str(f.get("acodec") or "none")
            if vcodec == "none" or acodec == "none":
                continue

            protocol = str(f.get("protocol") or "")
            if protocol not in {"https", "http"}:
                continue

            ext = str(f.get("ext") or "").lower()
            height = int(f.get("height") or 0)
            fps = float(f.get("fps") or 0)
            filesize = int(f.get("filesize") or f.get("filesize_approx") or 0)

            # Prefer MP4/H.264/AAC for downstream Shorts processing.
            mp4_score = 1 if ext == "mp4" else 0
            h264_score = 1 if "avc" in vcodec.lower() or "h264" in vcodec.lower() else 0
            aac_score = 1 if "mp4a" in acodec.lower() or "aac" in acodec.lower() else 0

            candidates.append(
                (mp4_score, h264_score, aac_score, height, fps, filesize, str(f.get("format_id")))
            )
        except Exception:
            continue

    if not candidates:
        return None

    # Highest quality progressive format, with MP4/H264/AAC preferred.
    candidates.sort(reverse=True)
    return candidates[0][-1]



def _pick_split_formats(info):
    """Pick one direct video format and one direct audio format.

    The current diagnostic shows this video exposes many video-only formats.
    Rather than asking yt-dlp to merge them internally, V30 downloads the
    two streams separately and invokes the bundled FFmpeg executable itself.
    """
    formats = info.get("formats") or []
    videos = []
    audios = []

    for f in formats:
        try:
            protocol = str(f.get("protocol") or "")
            if protocol not in {"https", "http"}:
                continue

            fmt_id = str(f.get("format_id") or "")
            if not fmt_id:
                continue

            vcodec = str(f.get("vcodec") or "none")
            acodec = str(f.get("acodec") or "none")
            ext = str(f.get("ext") or "").lower()
            height = int(f.get("height") or 0)
            fps = float(f.get("fps") or 0)
            filesize = int(f.get("filesize") or f.get("filesize_approx") or 0)
            tbr = float(f.get("tbr") or 0)

            if vcodec != "none" and acodec == "none":
                # Prefer MP4/H264, then resolution/fps/bitrate.
                mp4 = 1 if ext == "mp4" else 0
                h264 = 1 if ("avc" in vcodec.lower() or "h264" in vcodec.lower()) else 0
                # Keep the source manageable for a Shorts workflow.
                quality_height = min(height, 1080)
                videos.append((mp4, h264, quality_height, fps, tbr, filesize, fmt_id))
            elif vcodec == "none" and acodec != "none":
                m4a = 1 if ext in {"m4a", "mp4"} else 0
                aac = 1 if ("mp4a" in acodec.lower() or "aac" in acodec.lower()) else 0
                audios.append((m4a, aac, tbr, filesize, fmt_id))
        except Exception:
            continue

    if not videos or not audios:
        return None, None

    videos.sort(reverse=True)
    audios.sort(reverse=True)
    return videos[0][-1], audios[0][-1]


def _download_exact_format(url, format_id, output_path, extractor_args=None,
                           cookies_path="", progress_callback=None,
                           progress_base=0, progress_span=100):
    """Download one already-selected stream without yt-dlp postprocessing."""
    def hook(data):
        if not progress_callback:
            return
        try:
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                done = data.get("downloaded_bytes") or 0
                pct = (done / total * 100) if total else 0
                overall = progress_base + int(max(0, min(100, pct)) * progress_span / 100)
                progress_callback(overall, f"⬇️ YouTube download: {overall}%")
        except Exception:
            pass

    options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "format": str(format_id),
        "outtmpl": output_path,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "progress_hooks": [hook],
        "postprocessors": [],
        "js_runtimes": _yt_runtime_options(),
        "remote_components": ["ejs:github"],
    }
    if extractor_args:
        options["extractor_args"] = extractor_args
    _cookie_option(options, cookies_path)

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError(f"Format {format_id} did not create a usable file.")




def _merge_with_bundled_ffmpeg(video_path, audio_path, output_path):
    """Mux separately downloaded streams using imageio-ffmpeg's binary."""
    ffmpeg = FFMPEG or _ffmpeg_executable()
    if not ffmpeg or not Path(ffmpeg).exists():
        raise RuntimeError("Bundled FFmpeg executable is not available.")

    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600
    )
    if result.returncode != 0:
        tail = (result.stderr or "")[-2500:]
        raise RuntimeError("FFmpeg merge failed:\n" + tail)
    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("FFmpeg merge did not create the final MP4.")


def _download_split_streams(url, output_path, extractor_args=None,
                            cookies_path="", progress_callback=None):
    """V30 robust path: extract -> choose video/audio -> download separately -> mux."""
    probe_options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "skip_download": True,
        "js_runtimes": _yt_runtime_options(),
        "remote_components": ["ejs:github"],
        "socket_timeout": 30,
    }
    if extractor_args:
        probe_options["extractor_args"] = extractor_args
    _cookie_option(probe_options, cookies_path)

    with yt_dlp.YoutubeDL(probe_options) as ydl:
        info = ydl.extract_info(url, download=False)

    video_id, audio_id = _pick_split_formats(info)
    if not video_id or not audio_id:
        raise RuntimeError("Is client ke liye separate direct video/audio formats nahi mile.")

    tmp_dir = tempfile.mkdtemp(prefix="yt_split_")
    video_path = os.path.join(tmp_dir, "video_stream")
    audio_path = os.path.join(tmp_dir, "audio_stream")

    try:
        _download_exact_format(
            url, video_id, video_path, extractor_args, cookies_path,
            progress_callback, 0, 45
        )
        _download_exact_format(
            url, audio_id, audio_path, extractor_args, cookies_path,
            progress_callback, 45, 40
        )

        if progress_callback:
            progress_callback(88, "🔧 Video + audio merge ho raha hai...")

        _merge_with_bundled_ffmpeg(video_path, audio_path, output_path)

        if progress_callback:
            progress_callback(100, "✅ YouTube download complete.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)



def _youtube_client_attempts(include_bgutil=True):
    """Return genuinely different YouTube client configurations.

    V33 accidentally wrapped every client with _mweb_extractor_args(), which
    forced web_safari/tv/ios/etc. to use mweb underneath. V34 keeps each
    client independent and adds mweb+bgutil only as a separate final attempt.
    """
    attempts = [
        ("default", {}),
        ("web_safari", {"youtube": {"player_client": ["web_safari"]}}),
        ("web_embedded", {"youtube": {"player_client": ["web_embedded"]}}),
        ("tv_embedded", {"youtube": {"player_client": ["tv_embedded"]}}),
        ("tv", {"youtube": {"player_client": ["tv"]}}),
        ("ios", {"youtube": {"player_client": ["ios"]}}),
        ("android_vr", {"youtube": {"player_client": ["android_vr"]}}),
        ("mweb", {"youtube": {"player_client": ["mweb"]}}),
    ]

    if include_bgutil:
        base_url = _get_bgutil_base_url()
        if base_url:
            attempts.append((
                "mweb + bgutil PO-token",
                {
                    "youtube": {"player_client": ["mweb"]},
                    "youtubepot-bgutilhttp": {"base_url": base_url},
                },
            ))
    return attempts


def _provider_extractor_args(base_args=None):
    """Add the bgutil HTTP provider without changing the selected client."""
    args = dict(base_args or {})
    base_url = _get_bgutil_base_url()
    if base_url:
        args["youtubepot-bgutilhttp"] = {"base_url": base_url}
    return args


def _mweb_extractor_args(extra=None):
    """Build an mweb-specific config; do not use this for other clients."""
    args = dict(extra or {})
    youtube = dict(args.get("youtube") or {})
    youtube["player_client"] = ["mweb"]
    args["youtube"] = youtube
    return _provider_extractor_args(args)


def _provider_status():
    """Return a safe provider status string without exposing credentials."""
    base_url = _get_bgutil_base_url()
    if not base_url:
        return "PO-token provider: NOT AVAILABLE"
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/ping", timeout=2) as resp:
            if resp.status == 200:
                return f"PO-token provider: READY ({base_url})"
    except Exception as exc:
        return f"PO-token provider: UNREACHABLE ({type(exc).__name__})"
    return "PO-token provider: UNAVAILABLE"


def _probe_progressive_format(url, extractor_args=None, cookies_path=""):
    """Probe one selected client and return a concrete combined format id."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "js_runtimes": _yt_runtime_options(),
        "remote_components": ["ejs:github"],
        "socket_timeout": 30,
    }
    if extractor_args:
        options["extractor_args"] = extractor_args
    _cookie_option(options, cookies_path)

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return _pick_progressive_format(info)




def _ytdlp_download_smart(url, output_path, cookies_path="", progress_callback=None):
    """Download using independent YouTube clients, then mweb+bgutil."""
    errors = []

    for label, extractor_args in _youtube_client_attempts(include_bgutil=True):
        cleanup_temp_file(output_path)

        def _progress_hook(data):
            if not progress_callback:
                return
            try:
                status = data.get("status")
                if status == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate")
                    done = data.get("downloaded_bytes") or 0
                    if total:
                        pct = max(0, min(100, int(done * 100 / total)))
                        progress_callback(pct, f"⬇️ YouTube download: {pct}%")
                elif status == "finished":
                    progress_callback(100, "🔧 Download complete, file prepare ho rahi hai...")
            except Exception:
                pass

        try:
            # First ask this exact client for a progressive stream.
            progressive_id = _probe_progressive_format(
                url, extractor_args=extractor_args, cookies_path=cookies_path
            )

            if progressive_id:
                options = {
                    "quiet": True,
                    "no_warnings": False,
                    "noplaylist": True,
                    "format": progressive_id,
                    "merge_output_format": "mp4",
                    "ffmpeg_location": _ffmpeg_executable(),
                    "js_runtimes": _yt_runtime_options(),
                    "remote_components": ["ejs:github"],
                    "outtmpl": output_path,
                    "retries": 3,
                    "fragment_retries": 3,
                    "socket_timeout": 30,
                    "progress_hooks": [_progress_hook],
                }
                if extractor_args:
                    options["extractor_args"] = extractor_args
                _cookie_option(options, cookies_path)

                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl.download([url])

                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return label
                raise RuntimeError("Progressive download completed without output.")

            # No combined stream: download video/audio separately and mux.
            _download_split_streams(
                url, output_path, extractor_args, cookies_path, progress_callback
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return label
            raise RuntimeError("Split video/audio download did not create output.")

        except Exception as e:
            errors.append(f"{label}: {e}")

    cleanup_temp_file(output_path)
    raise RuntimeError(
        "YouTube download ke sabhi automatic methods fail hue:\n\n" +
        "\n\n".join(errors)
    )




def get_youtube_source_info(url, cookies_path=""):
    """Metadata: pytubefix first, then yt-dlp clients."""
    if not _youtube_url_ok(url):
        raise ValueError(
            "Valid YouTube video link paste karo, example: "
            "https://www.youtube.com/watch?v=..."
        )

    errors = []

    try:
        return _pytubefix_info(url)
    except Exception as e:
        errors.append(f"pytubefix: {e}")

    try:
        return _ytdlp_info_with_clients(url, cookies_path)
    except Exception as e:
        errors.append(f"yt-dlp clients: {e}")

    raise RuntimeError(
        "YouTube metadata read nahi ho paayi.\n\n" + "\n\n".join(errors)
    )


def download_youtube_source(url, output_path, cookies_path="", progress_callback=None):
    """Smart downloader: pytubefix first, then multi-client yt-dlp."""
    errors = []

    try:
        _pytubefix_download(url, output_path)
        return "pytubefix"
    except Exception as e:
        errors.append(f"pytubefix: {e}")
        cleanup_temp_file(output_path)

    try:
        return _ytdlp_download_smart(url, output_path, cookies_path, progress_callback)
    except Exception as e:
        errors.append(f"yt-dlp smart clients: {e}")
        cleanup_temp_file(output_path)

    raise RuntimeError(
        "Automatic YouTube download failed.\n\n" + "\n\n".join(errors)
    )


# ============================================================
# YOUTUBE OAUTH
# ============================================================

def get_google_client_config():
    if "google_oauth" not in st.secrets:
        return None

    cfg = st.secrets["google_oauth"]

    return {
        "web": {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [cfg["redirect_uri"]]
        }
    }


YOUTUBE_ACCOUNT_SLOTS = {
    "kids": {
        "name": "Kids Channel",
        "emoji": "🧸",
        "category_id": "24",
        "made_for_kids": True,
        "style_options": [
            "🌈 Colorful Kids",
            "✨ Cute & Soft",
            "🎉 Fun & Energetic",
            "🫧 Clean Kids",
            "🎬 Minimal"
        ],
    },
    "gaming": {
        "name": "Gaming Channel",
        "emoji": "🎮",
        "category_id": "20",
        "made_for_kids": False,
        "style_options": [
            "🎮 Gaming Clean",
            "⚡ Gaming Fast",
            "🔥 Gaming Highlights",
            "🎬 Minimal"
        ],
    },
    "story": {
        "name": "Story Podcast Channel",
        "emoji": "🎙️",
        "category_id": "22",
        "made_for_kids": False,
        "style_options": [
            "🎙️ Podcast Clean",
            "📖 Story Cinematic",
            "✨ Soft Story",
            "🎬 Minimal"
        ],
    },
}

def _migrate_old_youtube_token():
    """Move the old single-account token into the Kids account slot."""
    accounts = st.session_state.setdefault("youtube_accounts", {})
    old = st.session_state.get("youtube_token")

    if old and "kids" not in accounts:
        accounts["kids"] = old

    return accounts


def credentials_from_token(token_data):
    if not token_data:
        return None

    try:
        credentials = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=YOUTUBE_SCOPE
        )

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

            token_data.update({
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret
            })

        return credentials

    except Exception:
        return None


def credentials_from_session(account_key="kids"):
    accounts = _migrate_old_youtube_token()
    return credentials_from_token(accounts.get(account_key))


def get_youtube_service(account_key="kids"):
    credentials = credentials_from_session(account_key)

    if not credentials:
        return None

    return build(
        "youtube",
        "v3",
        credentials=credentials
    )


def _save_youtube_account(account_key, credentials):
    accounts = st.session_state.setdefault("youtube_accounts", {})

    previous = accounts.get(account_key, {})

    accounts[account_key] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        # Preserve the verified channel metadata across Streamlit reruns.
        "channel": previous.get("channel")
    }

    # Keep backward compatibility with the original single-account app.
    if account_key == "kids":
        st.session_state["youtube_token"] = accounts[account_key]


def _get_account_channel(account_key):
    accounts = st.session_state.setdefault("youtube_accounts", {})
    account_data = accounts.get(account_key, {})

    cached_channel = account_data.get("channel")
    if cached_channel:
        return cached_channel

    youtube = get_youtube_service(account_key)

    if not youtube:
        st.session_state[f"youtube_channel_error_{account_key}"] = (
            "OAuth token is not saved for this account slot."
        )
        return None

    try:
        response = youtube.channels().list(
            part="snippet",
            mine=True
        ).execute()

        items = response.get("items", [])

        if not items:
            st.session_state[f"youtube_channel_error_{account_key}"] = (
                "OAuth succeeded, but YouTube returned no channel for this Google account."
            )
            return None

        channel = items[0]
        account_data["channel"] = channel
        accounts[account_key] = account_data
        st.session_state[f"youtube_channel_error_{account_key}"] = ""
        return channel

    except Exception as e:
        st.session_state[f"youtube_channel_error_{account_key}"] = str(e)
        return None


def _current_account_key():
    return st.session_state.get("selected_youtube_account", "kids")



# ============================================================
# OAUTH ACCOUNT STATE
# ============================================================

OAUTH_STATE_MAX_AGE = 15 * 60  # 15 minutes


def _oauth_state_secret():
    """Use the Google OAuth client secret as a server-side signing key."""
    config = get_google_client_config()

    if not config:
        return ""

    return str(
        config["web"].get("client_secret", "")
    ).strip()


def create_account_oauth_state(account_key):
    """Create a signed OAuth state carrying the selected account slot."""
    if account_key not in YOUTUBE_ACCOUNT_SLOTS:
        account_key = "kids"

    payload = {
        "account": account_key,
        "nonce": secrets.token_urlsafe(24),
        "created": int(time.time())
    }

    payload_json = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True
    ).encode("utf-8")

    payload_b64 = base64.urlsafe_b64encode(
        payload_json
    ).decode("utf-8").rstrip("=")

    secret = _oauth_state_secret()

    if not secret:
        raise RuntimeError(
            "OAuth state signing secret available nahi hai."
        )

    signature = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256
    ).digest()

    signature_b64 = base64.urlsafe_b64encode(
        signature
    ).decode("utf-8").rstrip("=")

    return f"{payload_b64}.{signature_b64}"


def read_account_oauth_state(state_value):
    """Verify the signed OAuth state and recover the account slot."""
    try:
        if not state_value or "." not in state_value:
            return None

        payload_b64, signature_b64 = state_value.split(".", 1)
        secret = _oauth_state_secret()

        if not secret:
            return None

        expected_signature = hmac.new(
            secret.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256
        ).digest()

        received_signature = base64.urlsafe_b64decode(
            signature_b64 + "=" * (-len(signature_b64) % 4)
        )

        if not hmac.compare_digest(
            expected_signature,
            received_signature
        ):
            return None

        payload = json.loads(
            base64.urlsafe_b64decode(
                payload_b64 + "=" * (-len(payload_b64) % 4)
            ).decode("utf-8")
        )

        created = int(payload.get("created", 0))

        if (
            created <= 0
            or time.time() - created > OAUTH_STATE_MAX_AGE
        ):
            return None

        account_key = payload.get("account")

        if account_key not in YOUTUBE_ACCOUNT_SLOTS:
            return None

        return account_key

    except Exception:
        return None


def build_youtube_oauth_flow():
    """Create the OAuth flow using the exact redirect URI from Streamlit Secrets."""
    config = get_google_client_config()

    if not config:
        return None, None, "Google OAuth Secrets configured nahi hain."

    redirect_uri = config["web"]["redirect_uris"][0].strip()

    if not redirect_uri.startswith("https://"):
        return None, None, (
            "Redirect URI HTTPS hona chahiye. "
            "Streamlit Cloud ke liye https:// se start hona chahiye."
        )

    flow = Flow.from_client_config(
        config,
        scopes=YOUTUBE_SCOPE,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=False
    )

    return flow, redirect_uri, None


def start_youtube_oauth(account_key="kids"):
    if account_key not in YOUTUBE_ACCOUNT_SLOTS:
        account_key = "kids"

    flow, redirect_uri, error = build_youtube_oauth_flow()

    if error:
        st.error(error)
        return

    try:
        # IMPORTANT: the selected account is carried inside a signed OAuth
        # state value instead of relying only on Streamlit Session State.
        oauth_state = create_account_oauth_state(account_key)

        authorization_url, returned_state = flow.authorization_url(
            state=oauth_state,
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account consent"
        )

        # Keep these only as convenience/debug information.
        st.session_state["youtube_oauth_state"] = returned_state
        st.session_state["youtube_redirect_uri"] = redirect_uri

        st.link_button(
            "🔐 Continue with Google",
            authorization_url,
            use_container_width=True
        )

        st.caption(
            f"Google authorization: "
            f"{YOUTUBE_ACCOUNT_SLOTS[account_key]['emoji']} "
            f"{YOUTUBE_ACCOUNT_SLOTS[account_key]['name']}"
        )

    except Exception as e:
        st.error("OAuth start nahi ho paya.")
        st.code(str(e))

def handle_youtube_callback():
    code = st.query_params.get("code")
    returned_state = st.query_params.get("state")
    oauth_error = st.query_params.get("error")

    if oauth_error:
        st.error(f"Google OAuth error: {oauth_error}")
        st.query_params.clear()
        return False

    if not code:
        return False

    if not returned_state:
        st.error("OAuth state missing hai.")
        st.query_params.clear()
        return False

    config = get_google_client_config()

    if not config:
        st.error("Google OAuth configuration missing hai.")
        st.query_params.clear()
        return False

    # Recover the exact Kids/Gaming/Story slot from the signed OAuth state.
    # Do not rely on pending_youtube_account because OAuth can return in a
    # different Streamlit session.
    account_key = read_account_oauth_state(returned_state)

    if not account_key:
        st.error("OAuth state invalid ya expired hai.")
        st.info(
            "Connect button dobara click karke Google authorization "
            "complete karo."
        )
        st.query_params.clear()
        return False

    redirect_uri = config["web"]["redirect_uris"][0].strip()

    try:
        flow = Flow.from_client_config(
            config,
            scopes=YOUTUBE_SCOPE,
            state=returned_state,
            redirect_uri=redirect_uri,
            autogenerate_code_verifier=False
        )

        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Save the token to the exact account slot selected before OAuth.
        _save_youtube_account(
            account_key,
            credentials
        )

        # Immediately verify the newly authorized account and cache the actual
        # YouTube channel. This prevents a successful OAuth callback from being
        # shown as "Not connected" on the next rerun.
        verify_service = build(
            "youtube",
            "v3",
            credentials=credentials
        )
        verify_response = verify_service.channels().list(
            part="snippet",
            mine=True
        ).execute()
        verify_items = verify_response.get("items", [])

        if not verify_items:
            st.error(
                f"Google account authorize ho gaya, lekin "
                f"{YOUTUBE_ACCOUNT_SLOTS[account_key]['name']} ke liye "
                f"YouTube channel return nahi hua."
            )
            return False

        accounts = st.session_state.setdefault("youtube_accounts", {})
        accounts[account_key]["channel"] = verify_items[0]
        st.session_state[f"youtube_channel_error_{account_key}"] = ""

        st.session_state["selected_youtube_account"] = account_key
        st.session_state["youtube_oauth_state"] = None
        st.session_state["pending_youtube_account"] = None
        st.session_state["youtube_redirect_uri"] = redirect_uri

        st.query_params.clear()
        return True

    except Exception as e:
        error_text = str(e)
        st.error("YouTube authorization failed.")
        st.code(error_text)

        if "insufficientPermissions" in error_text or "insufficient authentication scopes" in error_text:
            st.warning(
                "OAuth token me required YouTube scopes nahi hain. "
                "Is version me youtube.upload + youtube.readonly dono request hote hain. "
                "Purana connection dobara Connect karke re-authorize karo."
            )
        else:
            st.info(
                "Google Cloud ka Authorized redirect URI aur Streamlit Secrets "
                "ka redirect_uri character-for-character same hona chahiye."
            )
        return False

def get_youtube_channel():
    youtube = get_youtube_service()

    if not youtube:
        return None

    try:
        response = youtube.channels().list(
            part="snippet",
            mine=True
        ).execute()

        items = response.get("items", [])

        return items[0] if items else None

    except Exception:
        return None


def upload_to_youtube(
    youtube,
    video_path,
    title,
    description,
    tags,
    privacy_status,
    made_for_kids,
    publish_at=None,
    category_id="24"
):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(category_id)
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids
        }
    }

    # Scheduled videos must be private and use a future RFC3339 publishAt time.
    if publish_at:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=8 * 1024 * 1024
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None

    while response is None:
        status, response = request.next_chunk()

        if status:
            st.progress(int(status.progress() * 100))

    return response


# ============================================================
# OAUTH CALLBACK
# ============================================================

if "code" in st.query_params or "error" in st.query_params:
    if handle_youtube_callback():
        st.success("✅ YouTube account connected!")
        time.sleep(1)
        st.rerun()


# ============================================================
# YOUTUBE CONNECTION
# ============================================================

with st.expander("📺 YouTube Accounts", expanded=False):
    st.subheader("📺 YouTube Accounts")

    _migrate_old_youtube_token()

    if "selected_youtube_account" not in st.session_state:
        st.session_state["selected_youtube_account"] = "kids"

    st.caption(
        "Teen channels connect karo: Kids, Gaming aur Story Podcast. "
        "Har channel ke liye Google authorization ek baar karna padega."
    )

    for account_key, meta in YOUTUBE_ACCOUNT_SLOTS.items():

        account_channel = _get_account_channel(account_key)

        c1, c2 = st.columns([3, 1])

        with c1:
            if account_channel:
                st.success(
                    f"{meta['emoji']} {meta['name']} — "
                    f"Connected: {account_channel['snippet']['title']}"
                )
            else:
                st.info(
                    f"{meta['emoji']} {meta['name']} — Not connected"
                )

        with c2:
            if account_channel:
                if st.button(
                    "Use",
                    key=f"use_{account_key}",
                    use_container_width=True
                ):
                    st.session_state["selected_youtube_account"] = account_key
                    st.rerun()
            else:
                if st.button(
                    "Connect",
                    key=f"connect_{account_key}",
                    use_container_width=True
                ):
                    start_youtube_oauth(account_key)

            if st.button(
                "Refresh",
                key=f"refresh_{account_key}",
                use_container_width=True
            ):
                st.session_state.pop(f"youtube_channel_error_{account_key}", None)
                st.session_state.setdefault("youtube_accounts", {}).get(
                    account_key, {}
                ).pop("channel", None)
                _get_account_channel(account_key)
                st.rerun()

    selected_account_key = _current_account_key()
    selected_account_meta = YOUTUBE_ACCOUNT_SLOTS[selected_account_key]
    youtube_service = get_youtube_service(selected_account_key)
    channel = _get_account_channel(selected_account_key)

    if channel:
        st.success(
            f"🎯 Selected: {selected_account_meta['emoji']} "
            f"{selected_account_meta['name']} → "
            f"{channel['snippet']['title']}"
        )
    else:
        channel_error = st.session_state.get(
            f"youtube_channel_error_{selected_account_key}",
            ""
        )

        st.warning(
            f"⚠️ {selected_account_meta['name']} connected nahi hai. "
            f"Upload ke liye pehle is channel ko connect karo."
        )

        if channel_error:
            st.caption(f"🔎 Connection check: {channel_error}")



st.divider()


# ============================================================
# CONTENT TYPE / CHANNEL
# ============================================================

with st.expander("🎯 Content Type", expanded=False):
    st.subheader("🎯 Content Type")

    content_type_options = [
        "🧸 Kids",
        "🎮 Gaming",
        "🎙️ Story Podcast"
    ]

    account_to_content = {
        "kids": "🧸 Kids",
        "gaming": "🎮 Gaming",
        "story": "🎙️ Story Podcast"
    }

    content_type = st.radio(
        "Choose where this content belongs",
        content_type_options,
        index=content_type_options.index(
            account_to_content.get(
                st.session_state.get("selected_youtube_account", "kids"),
                "🧸 Kids"
            )
        ),
        horizontal=True
    )

    content_to_account = {
        "🧸 Kids": "kids",
        "🎮 Gaming": "gaming",
        "🎙️ Story Podcast": "story"
    }

    content_account_key = content_to_account[content_type]
    content_meta = YOUTUBE_ACCOUNT_SLOTS[content_account_key]

    # Keep the selected upload account synchronized with the chosen content type.
    if st.session_state.get("selected_youtube_account") != content_account_key:
        st.session_state["selected_youtube_account"] = content_account_key
        selected_account_key = content_account_key
        selected_account_meta = YOUTUBE_ACCOUNT_SLOTS[selected_account_key]
        youtube_service = get_youtube_service(selected_account_key)
        channel = _get_account_channel(selected_account_key)

# ============================================================
# VIDEO SOURCE
# ============================================================

with st.expander("🎬 Video Source", expanded=False):
    st.subheader("🎬 Video Source")

    source_mode = st.radio(
        "Choose source",
        ["📤 Upload Video", "🔗 YouTube Video Link"],
        horizontal=True
    )

    uploaded_file = None
    youtube_url = ""
    youtube_info = None
    youtube_cookie_path = ""

    if source_mode == "📤 Upload Video":
        uploaded_file = st.file_uploader(
            "📤 Upload your video",
            type=["mp4", "mov", "mkv", "avi", "webm"]
        )
    else:
        youtube_url = st.text_input(
            "🔗 YouTube Video URL",
            placeholder="https://www.youtube.com/watch?v=..."
        )

        with st.expander("🛡️ YouTube bot-check help (optional)"):
            st.caption(
                "Agar YouTube 'Sign in to confirm you're not a bot' ya 403 de raha hai, "
                "apne khud ke YouTube account ka cookies.txt upload kar sakte ho. "
                "Cookies file kisi ke saath share mat karo."
            )
            youtube_cookie_file = st.file_uploader(
                "Upload your own cookies.txt (optional)",
                type=["txt"],
                key="youtube_cookie_file"
            )
            youtube_cookie_path = _youtube_cookie_path(youtube_cookie_file)
            if youtube_cookie_path:
                st.success("✅ Private cookies.txt loaded for this session.")

        if youtube_url.strip():
            try:
                youtube_info = get_youtube_source_info(youtube_url.strip(), youtube_cookie_path)
                st.success(
                    f"✅ Found: {youtube_info['title']} "
                    f"({int(youtube_info['duration'])} sec)"
                )
                st.caption("Downloader mode: pytubefix → yt-dlp multi-client → optional own cookies → optional bgutil")
            except Exception as e:
                st.warning(f"⚠️ YouTube link read nahi ho paayi: {e}")

with st.expander("📝 YouTube Naming", expanded=False):
    st.subheader("📝 YouTube Naming")

    default_topic = (
        youtube_info["title"]
        if youtube_info and youtube_info.get("title")
        else (
            "Gaming Highlights"
            if content_account_key == "gaming"
            else (
                "Story Podcast"
                if content_account_key == "story"
                else "Kids Fun Video"
            )
        )
    )

    topic = st.text_input(
        "Video Topic / Main Title",
        value=default_topic,
        placeholder="Example: Bingo Fun Day"
    )

    if not topic.strip():
        topic = (
            "Gaming Highlights"
            if content_account_key == "gaming"
            else (
                "Story Podcast"
                if content_account_key == "story"
                else "Kids Fun Video"
            )
        )


    # ========================================================
# SETTINGS
# ========================================================

with st.expander("⚙️ Shorts Settings", expanded=False):
    st.subheader("⚙️ Shorts Settings")

    col1, col2 = st.columns(2)

    with col1:
        chunk_duration = st.slider(
            "⏱️ Short Duration",
            15, 60, 35, 1
        )

    with col2:
        quality = st.selectbox(
            "🎥 Video Quality",
            ["720p - Faster", "1080p - Better Quality"]
        )


# ========================================================
# ZOOM CONTROL
# ========================================================

with st.expander("🔍 Main Video Zoom", expanded=False):
    st.subheader("🔍 Main Video Zoom")

    zoom_percent = st.slider(
        "Select how much the main video should be zoomed",
        min_value=70,
        max_value=150,
        value=100,
        step=5
    )

    st.caption(
        f"Current Zoom: {zoom_percent}%  •  "
        "100% = normal size"
    )


# ========================================================
# STYLE
# ========================================================

style_heading = {
    "kids": "🧸 Kids Style",
    "gaming": "🎮 Gaming Style",
    "story": "🎙️ Story Podcast Style"
}

with st.expander("🎨 Editing Style", expanded=False):
    st.subheader(
        style_heading.get(
            content_account_key,
            "🎨 Editing Style"
        )
    )

    style = st.selectbox(
        "Choose Editing Style",
        content_meta["style_options"]
    )


# ========================================================
# EFFECTS
# ========================================================

with st.expander("✨ Effects", expanded=False):
    st.subheader("✨ Effects")

    col1, col2 = st.columns(2)

    with col1:
        blur_background = st.checkbox(
            "🌫️ 70–80% Blur Background",
            value=True
        )

        sharpen_video = st.checkbox(
            "✨ Sharpen Main Video",
            value=True
        )

        audio_normalize = st.checkbox(
            "🔊 Normalize Audio",
            value=True
        )

    with col2:
        add_title_overlay = st.checkbox(
            "📝 Add Topic on Video",
            value=False
        )

        add_shorts_name = st.checkbox(
            "▶️ Add #Shorts to Names",
            value=True
        )

        create_thumbnails = st.checkbox(
            "🖼️ Create Thumbnails",
            value=True
        )

# ========================================================
# PODCAST / STORY FACE FOLLOW
# ========================================================

auto_face_follow = False
face_follow_zoom = 1.25

if content_account_key == "story":
    with st.expander("🎙️ Podcast Speaker Focus", expanded=False):
        st.subheader("🎙️ Podcast Speaker Focus")

        auto_face_follow = st.checkbox(
            "🎯 Auto Face Follow",
            value=True,
            help="9:16 crop automatically follows the most prominent visible face."
        )

        face_follow_zoom = st.slider(
            "🔍 Face Zoom",
            min_value=1.00,
            max_value=1.80,
            value=1.25,
            step=0.05
        )

        st.caption(
            "Single-speaker podcast ke liye best. Multiple faces me "
            "sabse prominent/largest face follow hoga."
        )


# ========================================================
# YOUTUBE SETTINGS
# ========================================================

with st.expander("📺 YouTube Upload Settings", expanded=False):
    st.subheader("📺 YouTube Upload Settings")

    upload_to_yt = st.checkbox(
        "🚀 Automatically Upload to YouTube",
        value=False
    )

    schedule_uploads = False
    schedule_start_date = None
    schedule_time = None
    schedule_gap_days = 1

    if upload_to_yt:

        if not youtube_service:
            st.warning(
                "⚠️ Pehle YouTube connect karo."
            )

        privacy_status = st.selectbox(
            "Visibility (Immediate Upload)",
            ["private", "unlisted", "public"],
            index=0
        )

        default_audience = (
            "Made for Kids"
            if content_meta["made_for_kids"]
            else "Not Made for Kids"
        )

        made_for_kids = st.radio(
            "Audience",
            ["Made for Kids", "Not Made for Kids"],
            index=(
                0 if default_audience == "Made for Kids" else 1
            )
        )

        made_for_kids_bool = (
            made_for_kids == "Made for Kids"
        )

        description_template = st.text_area(
            "YouTube Description",
            value=(
                "{title}\n\n"
                + (
                    "Fun kids video.\n\n#Shorts #Kids #Fun"
                    if content_account_key == "kids"
                    else (
                        "Gaming short video.\n\n#Shorts #Gaming"
                        if content_account_key == "gaming"
                        else "Story podcast short.\n\n#Shorts #Story #Podcast"
                    )
                )
            ),
            height=120
        )

        tags_text = st.text_input(
            "YouTube Tags",
            value=(
                (
                    "kids, kids video, kids shorts, fun, shorts"
                    if content_account_key == "kids"
                    else (
                        "gaming, gaming shorts, gameplay, shorts"
                        if content_account_key == "gaming"
                        else "story, podcast, storytelling, shorts"
                    )
                )
            )
        )


        # ====================================================
        # YOUTUBE SCHEDULING
        # ====================================================

        schedule_uploads = st.checkbox(
            "🗓️ Schedule Shorts on YouTube",
            value=False
        )

        if schedule_uploads:
            st.info(
                "Videos YouTube par abhi private upload hongi aur "
                "set date/time par automatically publish hongi."
            )

            schedule_start_date = st.date_input(
                "📅 Part 1 Publish Date",
                value=datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1),
                min_value=datetime.now(ZoneInfo("Asia/Kolkata")).date()
            )

            schedule_time = st.time_input(
                "⏰ Publish Time (India / IST)",
                value=dt_time(20, 0)
            )

            schedule_gap_days = st.number_input(
                "📆 Gap Between Parts (days)",
                min_value=1,
                max_value=30,
                value=1,
                step=1
            )


# ========================================================
# SOURCE PREPARATION / METADATA
# ========================================================

video_path = os.path.join(
    tempfile.gettempdir(),
    "kids_shorts_input.mp4"
)

if source_mode == "📤 Upload Video":

    if not uploaded_file:
        st.info("📤 Pehle video upload karo.")
        st.stop()

    with open(video_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    try:
        total_duration = get_video_duration(video_path)

        st.success(
            f"⏱️ Video Duration: "
            f"{int(total_duration)} seconds"
        )

    except Exception as e:
        st.error("Video read error")
        st.code(str(e))
        st.stop()

else:

    if not youtube_url.strip():
        st.info("🔗 Pehle YouTube video link paste karo.")
        st.stop()

    if not youtube_info:
        st.warning("⚠️ Valid YouTube video metadata load nahi hua.")
        st.stop()

    total_duration = float(youtube_info.get("duration") or 0)

    if total_duration <= 0:
        st.warning(
            "⚠️ Is YouTube video ki duration read nahi ho paayi."
        )
        st.stop()

    st.info(
        "ℹ️ Video abhi download nahi hua hai. "
        "Create button dabane par download hoga."
    )

if upload_to_yt and schedule_uploads:
    preview_parts = int(total_duration // chunk_duration)
    if total_duration % chunk_duration > 0:
        preview_parts += 1

    preview_tz = ZoneInfo("Asia/Kolkata")
    preview_base = datetime.combine(
        schedule_start_date,
        schedule_time,
        tzinfo=preview_tz
    )

    with st.expander("🗓️ Schedule Preview", expanded=False):
        preview_rows = []
        for pno in range(1, preview_parts + 1):
            dt = preview_base + timedelta(
                days=(pno - 1) * int(schedule_gap_days)
            )
            preview_rows.append(
                f"Part {pno} → {dt.strftime('%d-%m-%Y')} | {dt.strftime('%I:%M %p')}"
            )
        st.code("\n".join(preview_rows))

        if preview_base <= datetime.now(preview_tz):
            st.warning(
                "⚠️ Part 1 ka publish time past hai. Future date/time select karo."
            )


if quality.startswith("1080"):
    out_w, out_h = 1080, 1920
else:
    out_w, out_h = 720, 1280


# ========================================================
# CREATE BUTTON
# ========================================================

content_button_names = {
    "kids": "Kids Shorts",
    "gaming": "Gaming Shorts",
    "story": "Story Podcast Shorts"
}

current_content_name = content_button_names.get(
    content_account_key,
    "Shorts"
)

if source_mode == "🔗 YouTube Video Link":
    button_text = (
        f"🚀 Download + Create {current_content_name} + Upload"
        if upload_to_yt
        else
        f"🚀 Download + Create {current_content_name}"
    )
else:
    button_text = (
        f"🚀 Create {current_content_name} + Upload to YouTube"
        if upload_to_yt
        else
        f"🚀 Create {current_content_name}"
    )

st.divider()
st.markdown("### 🚀 Start")
process_clicked = st.button(
    button_text,
    type="primary",
    use_container_width=True,
    key="start_processing_button"
)

if process_clicked:

    # Re-read the selected account at click time. This avoids using a stale
    # service object after Content Type changes or an OAuth callback rerun.
    if upload_to_yt:
        selected_account_key = content_account_key
        youtube_service = get_youtube_service(selected_account_key)
        channel = _get_account_channel(selected_account_key)

        if not youtube_service or not channel:
            st.error(
                f"YouTube connected nahi hai for "
                f"{YOUTUBE_ACCOUNT_SLOTS[selected_account_key]['name']}."
            )
            st.info(
                "Upar isi channel ke saamne Connect/Connected status check karo."
            )
            st.stop()

    if source_mode == "🔗 YouTube Video Link" and not is_valid_youtube_url(
        youtube_url
    ):
        st.error("❌ Valid YouTube video URL paste karo.")
        st.stop()

    if upload_to_yt and schedule_uploads:
        schedule_tz = ZoneInfo("Asia/Kolkata")
        first_publish_dt = datetime.combine(
            schedule_start_date,
            schedule_time,
            tzinfo=schedule_tz
        )
        if first_publish_dt <= datetime.now(schedule_tz):
            st.error(
                "❌ Part 1 ka scheduled time past hai. Future date/time select karo."
            )
            st.stop()


    work_dir = tempfile.mkdtemp(
        prefix="kids_shorts_"
    )

    shorts_dir = os.path.join(
        work_dir, "Shorts"
    )

    thumbnails_dir = os.path.join(
        work_dir, "Thumbnails"
    )

    os.makedirs(shorts_dir, exist_ok=True)
    os.makedirs(thumbnails_dir, exist_ok=True)

    if source_mode == "🔗 YouTube Video Link":
        try:
            st.markdown("### ⬇️ YouTube Download Progress")
            download_progress = st.progress(0)
            download_status = st.empty()

            def update_download_progress(percent, message=""):
                percent = max(0, min(100, int(percent)))
                download_progress.progress(percent)
                if message:
                    download_status.caption(message)

            update_download_progress(1, "🔄 YouTube source video download start ho raha hai...")
            downloader_used = download_youtube_source(
                youtube_url.strip(),
                video_path,
                youtube_cookie_path,
                update_download_progress
            )
            update_download_progress(100, "✅ Download complete")
            st.success(
                f"✅ YouTube source video downloaded "
                f"using {downloader_used}."
            )
        except Exception as e:
            st.error("❌ YouTube video download failed.")
            st.code(str(e))
            error_text = str(e).lower()
            if "sign in to confirm you're not a bot" in error_text or "insufficientpo" in error_text or "403" in error_text or "requested format is not available" in error_text:
                st.warning(
                    "⚠️ YouTube extraction/download restriction detected. "
                    "Neeche automatic format diagnostic diya gaya hai, jisse pata chalega "
                    "ki Streamlit server ko kaunse formats actually mil rahe hain."
                )

            with st.expander("🔍 YouTube Format Diagnostic", expanded=True):
                st.caption(
                    "Diagnostic sirf available formats check karta hai. Cookies ya secret values display nahi hoti. "
                    "Agar sabhi clients extraction par fail ho jayein, problem YouTube bot/PO-token/JS-runtime side par hai."
                )
                with st.spinner("YouTube ke available formats check ho rahe hain..."):
                    diag_results, diag_failures = _youtube_format_diagnostic(
                        youtube_url.strip(),
                        youtube_cookie_path
                    )

                if diag_results:
                    for item in diag_results:
                        st.markdown(
                            f"**{item['client']}** — {item['format_count']} total formats, "
                            f"{item['video_format_count']} video formats"
                        )
                        if item["formats"]:
                            rows = []
                            for f in item["formats"]:
                                rows.append({
                                    "Format": f["id"],
                                    "Resolution": f["resolution"],
                                    "Ext": f["ext"],
                                    "FPS": f["fps"] or "",
                                    "Protocol": f["protocol"],
                                    "Audio": "Yes" if f["has_audio"] else "No",
                                    "Size MB": f["filesize_mb"] or "",
                                })
                            st.dataframe(rows, use_container_width=True, hide_index=True)
                        else:
                            st.caption("No usable video formats returned by this client.")

                if diag_failures:
                    st.markdown("**Client extraction errors:**")
                    st.code("\n".join(diag_failures))

                if not diag_results and diag_failures:
                    st.error(
                        "❌ Diagnostic bhi metadata extract nahi kar paaya. "
                        "Is case me server-side YouTube extraction restriction likely hai."
                    )

            shutil.rmtree(work_dir, ignore_errors=True)
            cleanup_temp_file(video_path)
            st.stop()

    generated_files = []
    generated_titles = []
    uploaded_videos = []

    try:

        num_parts = int(
            total_duration // chunk_duration
        )

        if total_duration % chunk_duration > 0:
            num_parts += 1

        st.info(
            f"🎬 {num_parts} Shorts create honge."
        )

        progress = st.progress(0)


        # =================================================
        # EACH PART
        # =================================================

        for index in range(num_parts):

            part_number = index + 1

            start = index * chunk_duration

            end = min(
                start + chunk_duration,
                total_duration
            )

            duration = end - start

            if duration < 5:
                continue


            youtube_title = make_title(
                topic,
                part_number,
                add_shorts_name
            )

            generated_titles.append(
                youtube_title
            )


            filename_title = (
                clean_filename(youtube_title)
                .replace("#", "")
                .replace(" ", "_")[:80]
            )

            output_filename = (
                f"{part_number:02d}_"
                f"{filename_title}.mp4"
            )

            output_path = os.path.join(
                shorts_dir,
                output_filename
            )


            st.write(
                f"🎬 **Part {part_number}/{num_parts}**"
            )


            # =================================================
            # PODCAST FACE FOLLOW PREPROCESS
            # =================================================

            face_follow_path = None

            if auto_face_follow and content_account_key == "story":
                face_follow_path = os.path.join(
                    work_dir,
                    f"face_follow_{part_number:03d}.mp4"
                )

                with st.spinner(
                    f"🎯 Part {part_number}: face tracking..."
                ):
                    create_face_follow_video(
                        video_path,
                        start,
                        duration,
                        face_follow_path,
                        out_w,
                        out_h,
                        face_follow_zoom
                    )

            # =================================================
            # VIDEO FILTER
            # =================================================

            if face_follow_path:
                # The preprocessor already created the 9:16 crop.
                video_filter = f"[1:v]format=yuv420p[v]"
            elif blur_background:

                background_filter = (
                    f"[0:v]"
                    f"scale={out_w}:{out_h}:"
                    f"force_original_aspect_ratio=increase,"
                    f"crop={out_w}:{out_h},"
                    f"boxblur=30:10"
                    f"[bg];"
                )

                # Zoom is applied ONLY to the clear foreground.
                # Background remains full-screen.
                zoom = zoom_percent / 100.0

                foreground_filter = (
                    f"[0:v]"
                    f"scale="
                    f"{out_w}:{out_h}:"
                    f"force_original_aspect_ratio=decrease"
                )

                if sharpen_video:
                    foreground_filter += (
                        ",unsharp=5:5:0.8:5:5:0.0"
                    )

                # Zoom foreground using scale + crop.
                foreground_filter += (
                    f",scale="
                    f"iw*{zoom}:ih*{zoom}"
                )

                # Keep foreground inside the canvas.
                foreground_filter += (
                    f",crop="
                    f"min(iw\\,{out_w}):"
                    f"min(ih\\,{out_h})"
                )

                foreground_filter += "[fg];"

                overlay_filter = (
                    f"[bg][fg]"
                    f"overlay="
                    f"(W-w)/2:"
                    f"(H-h)/2"
                    f"[v]"
                )

                video_filter = (
                    background_filter
                    + foreground_filter
                    + overlay_filter
                )

            else:

                zoom = zoom_percent / 100.0

                video_filter = (
                    f"[0:v]"
                    f"scale="
                    f"{out_w}:{out_h}:"
                    f"force_original_aspect_ratio=decrease,"
                    f"scale="
                    f"iw*{zoom}:ih*{zoom},"
                    f"crop="
                    f"min(iw\\,{out_w}):"
                    f"min(ih\\,{out_h}),"
                    f"pad="
                    f"{out_w}:{out_h}:"
                    f"(ow-iw)/2:"
                    f"(oh-ih)/2:"
                    f"black"
                    f"[v]"
                )


            # =================================================
            # TITLE OVERLAY
            # =================================================

            title_overlay_path = None

            if add_title_overlay:

                overlay_text = (
                    clean_filename(topic)
                    .replace(":", "")
                    .replace("'", "")
                    .replace('"', "")
                )[:45]

                title_overlay_path = os.path.join(
                    work_dir,
                    f"title_overlay_{part_number:03d}.png"
                )

                create_title_overlay_png(
                    overlay_text,
                    title_overlay_path,
                    width=out_w - 80,
                    height=90
                )

                # FFmpeg drawtext is not available in the Streamlit Cloud
                # imageio-ffmpeg binary, so use the portable overlay filter.
                title_input_label = "2:v" if face_follow_path else "1:v"
                video_filter += (
                    f";[v][{title_input_label}]overlay=40:40:format=auto[vout]"
                )
                final_video_label = "[vout]"

            else:
                final_video_label = "[v]"


            # =================================================
            # AUDIO
            # =================================================

            if audio_normalize:

                audio_filter = (
                    "[0:a]"
                    "loudnorm="
                    "I=-16:"
                    "TP=-1.5:"
                    "LRA=11"
                    "[a]"
                )

                audio_map = "[a]"

            else:

                audio_filter = ""
                audio_map = "0:a?"


            complete_filter = (
                video_filter
                + (";" + audio_filter if audio_filter else "")
            )


            command = [
                FFMPEG,
                "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", video_path,
            ]

            if face_follow_path:
                command += [
                    "-i", face_follow_path,
                ]

            if title_overlay_path:
                command += [
                    "-loop", "1",
                    "-i", title_overlay_path,
                ]

            command += [
                "-filter_complex", complete_filter,
                "-map", final_video_label,
                "-map", audio_map,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
            ]

            if title_overlay_path:
                command += ["-shortest"]

            command += [output_path]


            try:
                run_ffmpeg(command)

            except Exception as first_error:

                fallback_command = [
                    FFMPEG,
                    "-y",
                    "-ss", str(start),
                    "-t", str(duration),
                    "-i", video_path,
                ]

                if face_follow_path:
                    fallback_command += [
                        "-i", face_follow_path,
                    ]

                if title_overlay_path:
                    fallback_command += [
                        "-loop", "1",
                        "-i", title_overlay_path,
                    ]

                fallback_command += [
                    "-filter_complex", video_filter,
                    "-map", final_video_label,
                    "-an",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                ]

                if title_overlay_path:
                    fallback_command += ["-shortest"]

                fallback_command += [output_path]

                try:
                    run_ffmpeg(fallback_command)
                except Exception:
                    raise first_error

            cleanup_temp_file(face_follow_path)


            # =================================================
            # THUMBNAIL
            # =================================================

            if create_thumbnails:

                thumbnail_path = os.path.join(
                    thumbnails_dir,
                    f"Part_{part_number:02d}.jpg"
                )

                make_thumbnail(
                    output_path,
                    thumbnail_path
                )


            generated_files.append(
                output_path
            )


            # =================================================
            # YOUTUBE UPLOAD
            # =================================================

            if upload_to_yt:

                st.write(
                    "📤 Uploading to YouTube..."
                )

                tags = [
                    tag.strip()
                    for tag in tags_text.split(",")
                    if tag.strip()
                ]

                description = (
                    description_template
                    .replace(
                        "{title}",
                        youtube_title
                    )
                )

                with st.spinner(
                    f"Uploading Part {part_number}..."
                ):

                    response = upload_to_youtube(
                        youtube_service,
                        output_path,
                        youtube_title,
                        description,
                        tags,
                        "private" if schedule_uploads else privacy_status,
                        made_for_kids_bool,
                        (
                            (
                                datetime.combine(
                                    schedule_start_date,
                                    schedule_time,
                                    tzinfo=ZoneInfo("Asia/Kolkata")
                                )
                                + timedelta(
                                    days=(part_number - 1) * int(schedule_gap_days)
                                )
                            ).isoformat()
                            if schedule_uploads else None
                        ),
                        content_meta["category_id"]
                    )

                video_id = response.get("id")

                scheduled_dt = None
                if schedule_uploads:
                    scheduled_dt = (
                        datetime.combine(
                            schedule_start_date,
                            schedule_time,
                            tzinfo=ZoneInfo("Asia/Kolkata")
                        )
                        + timedelta(
                            days=(part_number - 1) * int(schedule_gap_days)
                        )
                    )

                uploaded_videos.append({
                    "part": part_number,
                    "title": youtube_title,
                    "id": video_id,
                    "scheduled": schedule_uploads,
                    "publish_at": scheduled_dt.isoformat() if scheduled_dt else "",
                    "url":
                        f"https://www.youtube.com/watch?v={video_id}"
                })

                if schedule_uploads:
                    st.success(
                        f"✅ Part {part_number} uploaded & scheduled for "
                        f"{scheduled_dt.strftime('%d-%m-%Y %I:%M %p')} (IST)."
                    )
                else:
                    st.success(
                        f"✅ Part {part_number} uploaded."
                    )

                # Free the large local MP4 immediately after YouTube accepts it.
                # This reduces Streamlit Cloud disk/RAM pressure on multi-part jobs.
                try:
                    if os.path.exists(output_path):
                        cleanup_temp_file(output_path)
                    if output_path in generated_files:
                        generated_files.remove(output_path)
                except Exception:
                    pass

            progress.progress(
                min(
                    int(
                        part_number / num_parts * 100
                    ),
                    100
                )
            )


        # =================================================
        # TITLES FILE
        # =================================================

        titles_file = os.path.join(
            work_dir,
            "Titles.txt"
        )

        with open(
            titles_file,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                "YouTube Shorts Titles\n"
                "=====================\n\n"
            )

            for i, title in enumerate(
                generated_titles,
                1
            ):
                f.write(
                    f"{i}. {title}\n"
                )


        # =================================================
        # UPLOAD REPORT
        # =================================================

        upload_report = None

        if uploaded_videos:

            upload_report = os.path.join(
                work_dir,
                "YouTube_Upload_Report.txt"
            )

            with open(
                upload_report,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    "YouTube Upload Report\n"
                    "=====================\n\n"
                )

                for item in uploaded_videos:

                    f.write(
                        f"Part: {item['part']}\n"
                        f"Title: {item['title']}\n"
                        f"Video ID: {item['id']}\n"
                        f"Scheduled: {item.get('scheduled', False)}\n"
                        f"Publish At: {item.get('publish_at', '')}\n"
                        f"URL: {item['url']}\n\n"
                    )


        # =================================================
        # ZIP
        # =================================================

        zip_path = os.path.join(
            work_dir,
            "Kids_Shorts_Bundle.zip"
        )

        with zipfile.ZipFile(
            zip_path,
            "w",
            zipfile.ZIP_DEFLATED
        ) as zipf:

            for file_path in generated_files:

                if (
                    os.path.exists(file_path)
                    and os.path.getsize(file_path) > 0
                ):

                    zipf.write(
                        file_path,
                        arcname=os.path.join(
                            "Shorts",
                            os.path.basename(file_path)
                        )
                    )


            if create_thumbnails:

                for file in os.listdir(
                    thumbnails_dir
                ):

                    file_path = os.path.join(
                        thumbnails_dir,
                        file
                    )

                    if os.path.isfile(file_path):

                        zipf.write(
                            file_path,
                            arcname=os.path.join(
                                "Thumbnails",
                                file
                            )
                        )


            zipf.write(
                titles_file,
                arcname="Titles.txt"
            )


            if upload_report:

                zipf.write(
                    upload_report,
                    arcname="YouTube_Upload_Report.txt"
                )


        # =================================================
        # SUCCESS
        # =================================================

        created_count = len(generated_titles)
        st.success(
            f"🎉 {created_count} Shorts successfully processed!"
        )

        # Remove the downloaded/uploaded source after the job completes.
        # This keeps Streamlit Cloud storage usage low for long videos.
        cleanup_temp_file(video_path)

        if upload_to_yt and not generated_files:
            st.info(
                "ℹ️ Uploaded Shorts ki local MP4 files processing ke baad "
                "automatically delete kar di gayi hain, taaki Streamlit Cloud "
                "memory/disk pressure se restart na ho."
            )


        with open(zip_path, "rb") as f:
            zip_data = f.read()


        st.download_button(
            label=(
                "📦 Download Shorts + Report ZIP"
                if not upload_to_yt
                else "📦 Download Titles + YouTube Report ZIP"
            ),
            data=zip_data,
            file_name="Kids_Shorts_Bundle.zip",
            mime="application/zip",
            use_container_width=True
        )


        if uploaded_videos:

            st.subheader("📺 YouTube Uploads")

            for item in uploaded_videos:

                if item.get("scheduled"):
                    dt_text = datetime.fromisoformat(
                        item["publish_at"]
                    ).strftime("%d-%m-%Y %I:%M %p")
                    st.markdown(
                        f"**Part {item['part']}** — {item['title']} — "
                        f"🗓️ {dt_text} IST"
                    )
                else:
                    st.markdown(
                        f"**Part {item['part']}** — "
                        f"{item['title']}"
                    )

                st.markdown(
                    f"[▶️ Open YouTube Video]({item['url']})"
                )


        st.subheader("📝 Generated Titles")

        for title in generated_titles:
            st.write("• " + title)


    except HttpError as e:

        st.error("❌ YouTube API Error")
        st.code(str(e))


    except Exception as e:

        st.error("❌ Processing Error")
        st.code(str(e))


    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )
