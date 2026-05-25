"""
utils/downloader.py
───────────────────
yt-dlp orqali media yuklash — per-client retry bilan.

Istisnolar:
  PermanentDownloadError — video mavjud emas / geo-blocked / copyright
  YouTubeAuthError       — YouTube bot taniqladi, cookies kerak
  YouTubePlayerError     — barcha player_client'lar muvaffaqiyatsiz
  Exception              — vaqtinchalik tarmoq / format xatoligi

YouTube strategiyasi:
  6 ta player_client navbatma-navbat sinab ko'riladi:
    default → web → web_safari → web_embedded → android → ios
  Har bir urinish:
    - cookiefile ishlatiladi (agar mavjud bo'lsa)
    - To'liq Chrome sarlavhalari
    - retry=5, sleep_interval=1-3s
  "Failed to extract any player response" → keyingi client
  Bot detection xatosi   → darhol YouTubeAuthError
  Permanent xato         → darhol PermanentDownloadError

Instagram/TikTok: faqat "default" client ishlatiladi (multi-client shart emas).

Sifat darajalari (QUALITY_PRESETS):
  720p → 480p → 360p (handler sifatni tanlaydi)
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
# YouTube player_client navbati
# ──────────────────────────────────────────────────────────

_YOUTUBE_PLAYER_CLIENTS: list[str] = [
    "default",
    "web",
    "web_safari",
    "web_embedded",
    "android",
    "ios",
]

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
    "Accept-Language":    "en-US,en;q=0.9,uz;q=0.8,ru;q=0.7",
    "Accept-Encoding":    "gzip, deflate, br",
    "Referer":            "https://www.youtube.com/",
    "Origin":             "https://www.youtube.com",
    "Sec-Fetch-Dest":     "document",
    "Sec-Fetch-Mode":     "navigate",
    "Sec-Fetch-Site":     "same-origin",
    "Sec-Ch-Ua":          '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile":   "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# ──────────────────────────────────────────────────────────
# Platforma aniqlash
# ──────────────────────────────────────────────────────────

_PLATFORM_MAP = {
    ("youtube.com", "youtu.be"):                       "youtube",
    ("instagram.com",):                                "instagram",
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

# ──────────────────────────────────────────────────────────
# Xato klassifikatsiya kalit so'zlari
# ──────────────────────────────────────────────────────────

_BOT_DETECTION_KEYWORDS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "not a bot",
    "bot detection",
    "please sign in",
    "verification required",
)

_PERMANENT_KEYWORDS = (
    "private", "unavailable", "removed", "not available",
    "geo", "restricted", "age", "copyright", "404",
    "video unavailable", "this video is not available",
    "confirm your age", "members only", "account required",
)

_PLAYER_RESPONSE_KEYWORDS = (
    "failed to extract any player response",
    "player response",
    "no player response",
    "could not find match for",
    "unable to extract",
    "nsig extraction failed",
    "sign in to watch",
)


# ──────────────────────────────────────────────────────────
# Istisnolar
# ──────────────────────────────────────────────────────────

class PermanentDownloadError(Exception):
    """Video mavjud emas, himoyalangan yoki geo-blocked — retry befoyda."""


class YouTubeAuthError(Exception):
    """YouTube bot taniqlash. cookies.txt qo'shilishi/yangilanishi kerak."""


class YouTubePlayerError(Exception):
    """Barcha player_client urinishlari muvaffaqiyatsiz — vaqtinchalik."""


# ──────────────────────────────────────────────────────────
# Yordamchi funksiyalar
# ──────────────────────────────────────────────────────────

def _classify_error(msg: str) -> str:
    """Xato turinini aniqlaydi: 'bot' | 'permanent' | 'player' | 'temp'"""
    lower = msg.lower()
    if any(kw in lower for kw in _BOT_DETECTION_KEYWORDS):
        return "bot"
    if any(kw in lower for kw in _PERMANENT_KEYWORDS):
        return "permanent"
    if any(kw in lower for kw in _PLAYER_RESPONSE_KEYWORDS):
        return "player"
    return "temp"


def _find_output_file(directory: str, prefix: str) -> Optional[str]:
    """Prefiks bo'yicha papkada fayl qidiradi."""
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix):
                return os.path.join(directory, name)
    except OSError:
        pass
    return None


def _cleanup_prefix(directory: str, prefix: str) -> None:
    """Prefiks bilan boshlangan barcha fayllarni o'chiradi (partial downloads)."""
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass
    except OSError:
        pass


def _cookie_opts() -> dict:
    """cookiefile opsiyasini qaytaradi (agar mavjud va yoqilgan bo'lsa)."""
    if YOUTUBE_COOKIES_ENABLED and os.path.exists(COOKIES_PATH):
        logger.info(f"Using YouTube cookies: {os.path.abspath(COOKIES_PATH)}")
        return {"cookiefile": COOKIES_PATH}
    if YOUTUBE_COOKIES_ENABLED and not os.path.exists(COOKIES_PATH):
        logger.debug(f"YOUTUBE_COOKIES_ENABLED=true lekin '{COOKIES_PATH}' topilmadi")
    return {}


def _build_opts(client: str) -> dict:
    """
    Berilgan player_client uchun to'liq yt-dlp sozlamalarini qaytaradi.
    cookiefile, brauzer sarlavhalari, retry, geo_bypass va boshqalar.
    """
    opts: dict = {
        # ── Umumiy ──────────────────────────────────────────
        "quiet":               True,
        "no_warnings":         True,
        "socket_timeout":      60,
        "nocheckcertificate":  True,   # SSL sertifikat tekshiruvini o'chirish
        "geo_bypass":          True,   # Geo-bloklarni bypass qilish
        "extract_flat":        False,
        "skip_download":       False,

        # ── Retry ────────────────────────────────────────────
        "retries":             5,
        "fragment_retries":    5,
        "file_access_retries": 3,

        # ── Anti-bot pauza ───────────────────────────────────
        "sleep_interval":          1,
        "max_sleep_interval":      3,
        "sleep_interval_requests": 1,

        # ── Brauzer sarlavhalari ──────────────────────────────
        "http_headers": _HEADERS,

        # ── YouTube player_client (faqat bitta sinaydi) ───────
        "extractor_args": {
            "youtube": {
                "player_client": [client],
            }
        },
    }
    # Cookies qo'shish
    opts.update(_cookie_opts())
    return opts


# ──────────────────────────────────────────────────────────
# Video yuklab olish — per-client retry
# ──────────────────────────────────────────────────────────

async def download_raw_video(url: str, fmt: str) -> str:
    """
    Berilgan yt-dlp format matni bilan xom MP4 yuklab oladi.

    YouTube uchun 6 ta player_client navbatma-navbat sinab ko'riladi.
    Har birida "Failed to extract any player response" xatosi bo'lsa,
    keyingi client'ga o'tiladi.

    Qaytaradi: MP4 fayl yo'li (mutlaq).
    Ko'taradi:
      YouTubeAuthError    — bot taniqlash (cookies kerak)
      PermanentDownloadError — video mavjud emas/himoyalangan
      YouTubePlayerError  — barcha client'lar muvaffaqiyatsiz
      Exception           — vaqtinchalik boshqa xato
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

    platform   = detect_platform(url)
    is_youtube = platform == "youtube"
    clients    = _YOUTUBE_PLAYER_CLIENTS if is_youtube else ["default"]

    last_error:      str = "Noma'lum xato"
    last_error_type: str = "temp"
    player_errors:   int = 0   # Nechta client "player response" xatosi berdi

    for idx, client in enumerate(clients):
        fid    = uuid.uuid4().hex[:10]
        prefix = f"vid_{fid}"
        tpl    = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

        opts = _build_opts(client)
        opts.update({
            "format":              fmt,
            "outtmpl":             tpl,
            "merge_output_format": "mp4",
        })

        logger.info(
            f"[YouTube:{client}] urinish {idx + 1}/{len(clients)}: {url[:60]}..."
            if is_youtube else f"Yuklab olinmoqda: {url[:60]}..."
        )

        result: dict = {"path": None, "error": None, "error_type": "temp"}

        # Closure uchun local alias (loop variable capture'ni oldini olish)
        _opts   = opts
        _result = result

        def _run() -> None:
            try:
                with yt_dlp.YoutubeDL(_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        _result["error"] = "Kontent ma'lumoti olinmadi"
                        return
                    raw  = ydl.prepare_filename(info)
                    base = os.path.splitext(raw)[0]
                    mp4  = base + ".mp4"
                    if os.path.exists(mp4):
                        _result["path"] = mp4
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                _result["error"]      = msg
                _result["error_type"] = _classify_error(msg)
            except Exception as exc:
                _result["error"] = str(exc)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run)

        # ── Muvaffaqiyatli ──────────────────────────────────
        if result["path"] and os.path.exists(result["path"]):
            return result["path"]

        # yt-dlp ba'zan kutilmagan kengaytma ishlatadi
        found = _find_output_file(DOWNLOAD_PATH, prefix)
        if found:
            return found

        # ── Xato tahlili ────────────────────────────────────
        error      = result["error"] or "Noma'lum xato"
        error_type = result["error_type"]

        # Partial fayllarni tozalash
        _cleanup_prefix(DOWNLOAD_PATH, prefix)

        if error_type == "bot":
            # Bot taniqlash — boshqa client sinash befoyda
            logger.warning(f"[{client}] Bot taniqlash xatosi, to'xtaymiz.")
            raise YouTubeAuthError(error)

        if error_type == "permanent":
            # Video mavjud emas — retry befoyda
            logger.warning(f"[{client}] Permanent xato: {error[:80]}")
            raise PermanentDownloadError(error)

        if error_type == "player":
            player_errors += 1
            next_msg = "Keyingi clientga o'tamiz." if idx + 1 < len(clients) else "Barcha clientlar tugadi."
            logger.warning(f"[{client}] Player response xatosi ({player_errors}). {next_msg}")
        else:
            logger.warning(f"[{client}] Xato ({error_type}): {error[:80]}")

        last_error      = error
        last_error_type = error_type

    # ── Barcha clientlar muvaffaqiyatsiz ────────────────────
    if is_youtube and player_errors == len(clients):
        raise YouTubePlayerError(
            "Barcha player_client urinishlari muvaffaqiyatsiz: "
            f"{last_error[:100]}"
        )

    if last_error_type == "player":
        raise YouTubePlayerError(last_error)

    raise Exception(last_error)


# ──────────────────────────────────────────────────────────
# Audio yuklab olish — per-client retry
# ──────────────────────────────────────────────────────────

async def download_audio(url: str) -> str:
    """
    URL dan ovoz ajratib, MP3 128 kbps formatida yuklab oladi.
    YouTube uchun per-client retry ishlatiladi.
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

    platform   = detect_platform(url)
    is_youtube = platform == "youtube"
    clients    = _YOUTUBE_PLAYER_CLIENTS if is_youtube else ["default"]

    # AUDIO_BITRATE: "128k" → "128"  (yt-dlp raqam kutadi)
    bitrate_num = AUDIO_BITRATE.rstrip("kK")

    last_error:      str = "Noma'lum xato"
    last_error_type: str = "temp"
    player_errors:   int = 0

    for idx, client in enumerate(clients):
        fid    = uuid.uuid4().hex[:10]
        prefix = f"aud_{fid}"
        tpl    = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

        opts = _build_opts(client)
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

        logger.info(
            f"[YouTube:{client}] audio urinish {idx + 1}/{len(clients)}"
            if is_youtube else "Audio yuklab olinmoqda..."
        )

        result: dict = {"path": None, "error": None, "error_type": "temp"}
        _opts   = opts
        _result = result

        def _run() -> None:
            try:
                with yt_dlp.YoutubeDL(_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        _result["error"] = "Kontent ma'lumoti olinmadi"
                        return
                    raw  = ydl.prepare_filename(info)
                    base = os.path.splitext(raw)[0]
                    mp3  = base + ".mp3"
                    if os.path.exists(mp3):
                        _result["path"] = mp3
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                _result["error"]      = msg
                _result["error_type"] = _classify_error(msg)
            except Exception as exc:
                _result["error"] = str(exc)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run)

        if result["path"] and os.path.exists(result["path"]):
            return result["path"]

        found = _find_output_file(DOWNLOAD_PATH, prefix)
        if found:
            return found

        error      = result["error"] or "Noma'lum xato"
        error_type = result["error_type"]

        _cleanup_prefix(DOWNLOAD_PATH, prefix)

        if error_type == "bot":
            raise YouTubeAuthError(error)
        if error_type == "permanent":
            raise PermanentDownloadError(error)
        if error_type == "player":
            player_errors += 1
            logger.warning(f"[{client}] Player response xatosi, keyingisiga o'tamiz")

        last_error      = error
        last_error_type = error_type

    if is_youtube and player_errors > 0:
        raise YouTubePlayerError(last_error)

    raise Exception(last_error)


# ──────────────────────────────────────────────────────────
# Moslik qatlamasi
# ──────────────────────────────────────────────────────────

async def download_media(url: str, media_type: str = "video") -> str:
    """Eski kod uchun moslik. Yangi kod download_raw_video / download_audio ishlatsin."""
    if media_type == "audio":
        return await download_audio(url)
    return await download_raw_video(url, QUALITY_PRESETS[0][1])
