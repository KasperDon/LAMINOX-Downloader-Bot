"""
utils/downloader.py
───────────────────
yt-dlp orqali media yuklash.

  QUALITY_PRESETS       — video sifat darajalari (720p → 480p → 360p)
  download_raw_video()  — berilgan yt-dlp format bilan xom MP4 yuklab oladi
  download_audio()      — MP3 128 kbps yuklab ajratib beradi
  detect_platform()     — platformani aniqlaydi
  PermanentDownloadError — qayta urinish befoyda bo'lgan holatlar
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

import yt_dlp

from config import AUDIO_BITRATE, DOWNLOAD_PATH

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_PLATFORM_MAP = {
    ("youtube.com", "youtu.be"): "youtube",
    ("instagram.com",):          "instagram",
    ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"): "tiktok",
}

# ── Cascading sifat darajalari ────────────────────────────
# Har bir preset: (yorliq, yt-dlp format matni)
# Handler shu tartibda sinab ko'radi: 720p → 480p → 360p
QUALITY_PRESETS: list[tuple[str, str]] = [
    (
        "720p",
        (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[ext=mp4][height<=720]"
            "/best[height<=720]"
            "/best"
        ),
    ),
    (
        "480p",
        (
            "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]"
            "/bestvideo[height<=480]+bestaudio"
            "/best[ext=mp4][height<=480]"
            "/best[height<=480]"
            "/best"
        ),
    ),
    (
        "360p",
        (
            "bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]"
            "/bestvideo[height<=360]+bestaudio"
            "/best[ext=mp4][height<=360]"
            "/best[height<=360]"
            "/best"
        ),
    ),
]

# Permanent xatolar: qayta urinish befoyda
_PERMANENT_KEYWORDS = (
    "private", "unavailable", "removed", "not available",
    "geo", "restricted", "age", "copyright", "404",
    "video unavailable", "this video is not available",
)


class PermanentDownloadError(Exception):
    """Video mavjud emas, himoyalangan yoki geo-blocked — retry befoyda."""


# ──────────────────────────────────────────────────────────
# Yordamchilar
# ──────────────────────────────────────────────────────────

def detect_platform(url: str) -> str:
    u = url.lower()
    for domains, name in _PLATFORM_MAP.items():
        if any(d in u for d in domains):
            return name
    return "unknown"


def _find_output_file(directory: str, prefix: str) -> Optional[str]:
    """Prefiks bo'yicha papkada fayl qidiradi (yt-dlp ba'zan kengaytma o'zgartirar)."""
    for name in os.listdir(directory):
        if name.startswith(prefix):
            return os.path.join(directory, name)
    return None


# ──────────────────────────────────────────────────────────
# Video yuklab olish (xom, FFmpeg siqishsiz)
# ──────────────────────────────────────────────────────────

async def download_raw_video(url: str, fmt: str) -> str:
    """
    Berilgan yt-dlp format matni bilan xom MP4 yuklab oladi.

    Qaytaradi: MP4 fayl yo'li (mutlaq).
    Ko'taradi:
      PermanentDownloadError — video mavjud emas / himoyalangan
      Exception              — vaqtinchalik xato (qayta urinish mumkin)
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    fid    = uuid.uuid4().hex[:10]
    prefix = f"vid_{fid}"
    tpl    = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

    opts = {
        "format":               fmt,
        "outtmpl":              tpl,
        "merge_output_format":  "mp4",
        "quiet":                True,
        "no_warnings":          True,
        "socket_timeout":       60,
        "retries":              2,
        "http_headers":         {"User-Agent": _UA},
    }

    result: dict = {"path": None, "error": None, "permanent": False}

    def _run() -> None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    result["error"] = "Kontent ma'lumoti olinmadi"
                    return
                raw  = ydl.prepare_filename(info)
                base = os.path.splitext(raw)[0]
                mp4  = base + ".mp4"
                if os.path.exists(mp4):
                    result["path"] = mp4
        except yt_dlp.utils.DownloadError as exc:
            msg = str(exc)
            result["error"]     = msg
            result["permanent"] = any(kw in msg.lower() for kw in _PERMANENT_KEYWORDS)
        except Exception as exc:
            result["error"] = str(exc)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run)

    if result["error"]:
        if result["permanent"]:
            raise PermanentDownloadError(result["error"])
        raise Exception(result["error"])

    if result["path"] and os.path.exists(result["path"]):
        return result["path"]

    # Fallback: yt-dlp ba'zan kutilmagan kengaytma ishlatadi
    found = _find_output_file(DOWNLOAD_PATH, prefix)
    if found:
        return found

    raise Exception("Fayl yuklab olingandan keyin topilmadi")


# ──────────────────────────────────────────────────────────
# Audio yuklab olish (MP3 128 kbps)
# ──────────────────────────────────────────────────────────

async def download_audio(url: str) -> str:
    """
    URL dan ovoz ajratib, MP3 128 kbps formatida yuklab oladi.
    Qaytaradi: MP3 fayl yo'li (mutlaq).
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    fid    = uuid.uuid4().hex[:10]
    prefix = f"aud_{fid}"
    tpl    = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

    # AUDIO_BITRATE: "128k" → "128"  (yt-dlp raqam kutadi)
    bitrate_num = AUDIO_BITRATE.rstrip("kK")

    opts = {
        "format":       "bestaudio/best",
        "outtmpl":      tpl,
        "quiet":        True,
        "no_warnings":  True,
        "socket_timeout": 60,
        "retries":      3,
        "postprocessors": [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": bitrate_num,
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "http_headers": {"User-Agent": _UA},
    }

    result: dict = {"path": None, "error": None}

    def _run() -> None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    result["error"] = "Kontent ma'lumoti olinmadi"
                    return
                raw  = ydl.prepare_filename(info)
                base = os.path.splitext(raw)[0]
                mp3  = base + ".mp3"
                if os.path.exists(mp3):
                    result["path"] = mp3
        except yt_dlp.utils.DownloadError as exc:
            result["error"] = str(exc)
        except Exception as exc:
            result["error"] = str(exc)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run)

    if result["error"]:
        raise Exception(result["error"])

    if result["path"] and os.path.exists(result["path"]):
        return result["path"]

    found = _find_output_file(DOWNLOAD_PATH, prefix)
    if found:
        return found

    raise Exception("MP3 fayli yuklab olingandan keyin topilmadi")


# ──────────────────────────────────────────────────────────
# Moslik qatlamasi (eski import'lar uchun)
# ──────────────────────────────────────────────────────────

async def download_media(url: str, media_type: str = "video") -> str:
    """Eski kod uchun moslik. Yangi kod download_raw_video / download_audio ishlatsin."""
    if media_type == "audio":
        return await download_audio(url)
    return await download_raw_video(url, QUALITY_PRESETS[0][1])
