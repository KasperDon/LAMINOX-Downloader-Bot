"""
utils/downloader.py
───────────────────
yt-dlp orqali media yuklash — anti-bot himoya bilan.

  QUALITY_PRESETS       — video sifat darajalari (720p → 480p → 360p)
  download_raw_video()  — berilgan yt-dlp format bilan xom MP4 yuklab oladi
  download_audio()      — MP3 128 kbps yuklab ajratib beradi
  detect_platform()     — platformani aniqlaydi
  PermanentDownloadError — qayta urinish befoyda bo'lgan holatlar

Anti-bot himoya:
  • To'liq brauzer sarlavhalari (User-Agent, Accept-Language, Referer, ...)
  • YouTube player_client: web + android + ios (navbatma-navbat)
  • sleep_interval: so'rovlar orasida kutish
  • Agar cookies.txt mavjud bo'lsa — avtomatik ishlatiladi
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

import yt_dlp

from config import (
    AUDIO_BITRATE,
    COOKIES_PATH,
    DOWNLOAD_PATH,
    YOUTUBE_COOKIES_ENABLED,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Brauzer sarlavhalari — bot aniqlashdan himoya
# ──────────────────────────────────────────────────────────

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,uz;q=0.8,ru;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.youtube.com/",
    "Origin":          "https://www.youtube.com",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Ch-Ua":       '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile":   "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# ──────────────────────────────────────────────────────────
# Platforma aniqlash
# ──────────────────────────────────────────────────────────

_PLATFORM_MAP = {
    ("youtube.com", "youtu.be"): "youtube",
    ("instagram.com",):          "instagram",
    ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"): "tiktok",
}


def detect_platform(url: str) -> str:
    u = url.lower()
    for domains, name in _PLATFORM_MAP.items():
        if any(d in u for d in domains):
            return name
    return "unknown"


# ──────────────────────────────────────────────────────────
# Cascading sifat darajalari
# ──────────────────────────────────────────────────────────

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

# Permanent xatolar uchun kalit so'zlar
_PERMANENT_KEYWORDS = (
    "private", "unavailable", "removed", "not available",
    "geo", "restricted", "age", "copyright", "404",
    "video unavailable", "this video is not available",
    "confirm your age",
)


class PermanentDownloadError(Exception):
    """Video mavjud emas, himoyalangan yoki geo-blocked — retry befoyda."""


# ──────────────────────────────────────────────────────────
# Umumiy yt-dlp sozlamalari
# ──────────────────────────────────────────────────────────

def _cookie_opts() -> dict:
    """
    Agar YOUTUBE_COOKIES_ENABLED=true va cookies.txt mavjud bo'lsa,
    cookiefile opsiyasini qaytaradi va logga yozadi.
    """
    if YOUTUBE_COOKIES_ENABLED and os.path.exists(COOKIES_PATH):
        logger.info(f"Using YouTube cookies: {os.path.abspath(COOKIES_PATH)}")
        return {"cookiefile": COOKIES_PATH}
    if YOUTUBE_COOKIES_ENABLED and not os.path.exists(COOKIES_PATH):
        logger.debug(
            f"YOUTUBE_COOKIES_ENABLED=true lekin {COOKIES_PATH} topilmadi — "
            "cookiesiz davom etiladi"
        )
    return {}


def _common_opts() -> dict:
    """
    Barcha yuklab olishlarda ishlatiladigan asosiy sozlamalar:
      - To'liq brauzer sarlavhalari
      - YouTube player_client: web + android + ios
      - Retry va uyqu oraliq vaqtlari
      - Cookies (agar mavjud bo'lsa)
    """
    opts: dict = {
        # ── Umumiy ────────────────────────────────────────
        "quiet":            True,
        "no_warnings":      True,
        "socket_timeout":   60,

        # ── Retry sozlamalari ──────────────────────────────
        "retries":          5,
        "fragment_retries": 5,
        "file_access_retries": 3,

        # ── Bot aniqlashga qarshi uyqu ─────────────────────
        # Har bir so'rov orasida 1–3 soniya kutadi
        "sleep_interval":      1,
        "max_sleep_interval":  3,
        "sleep_interval_requests": 1,

        # ── Brauzer sarlavhalari ──────────────────────────
        "http_headers": _HEADERS,

        # ── YouTube maxsus sozlamalar ─────────────────────
        # player_client: web (cookiessiz ham ishlaydi),
        #   android va ios — zaxira variantlar
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android", "ios"],
            }
        },
    }
    # Cookies fayl mavjud bo'lsa qo'shib qo'y
    opts.update(_cookie_opts())
    return opts


# ──────────────────────────────────────────────────────────
# Yordamchi
# ──────────────────────────────────────────────────────────

def _find_output_file(directory: str, prefix: str) -> Optional[str]:
    """Prefiks bo'yicha papkada fayl qidiradi."""
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix):
                return os.path.join(directory, name)
    except OSError:
        pass
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

    opts = _common_opts()
    opts.update({
        "format":              fmt,
        "outtmpl":             tpl,
        "merge_output_format": "mp4",
    })

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

    opts = _common_opts()
    opts.update({
        "format":  "bestaudio/best",
        "outtmpl": tpl,
        "postprocessors": [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": bitrate_num,
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
    })

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
