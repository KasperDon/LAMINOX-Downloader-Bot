"""
utils/downloader.py
───────────────────
yt-dlp orqali media yuklash — per-client retry + proxy fallback + timeout bilan.

YouTube strategiyasi (2025):
  YouTube datacenter IP'larini bloklamoqda.
  Faqat PO token talab qilmaydigan clientlar ishlatiladi:
    ios → android → mweb
  Har bir client uchun maksimal 60 soniya timeout.
  Agar 60s da javob bo'lmasa → keyingi clientga o'tiladi.

  sleep_interval = 0 (kutish o'chirilgan — tezlash uchun)
  retries = 3 (tez muvaffaqiyatsiz bo'lish)

Istisnolar:
  PermanentDownloadError — video mavjud emas / geo-blocked / copyright
  YouTubeAuthError       — YouTube bot taniqladi, cookies kerak
  YouTubePlayerError     — barcha player_client'lar muvaffaqiyatsiz
  Exception              — vaqtinchalik tarmoq / format xatoligi

Proxy fallback:
  PROXY_URL o'rnatilgan bo'lsa avval proxy bilan urinadi.
  Proxy xatosi bo'lsa → proxysiz qayta urinadi.
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
    PROXY_URL,
    YOUTUBE_COOKIES_ENABLED,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# YouTube player_client navbati
# ──────────────────────────────────────────────────────────
# 2025: YouTube web/web_safari/web_embedded uchun PO token talab qiladi.
# ios / android / mweb — PO tokensiz ishlaydi → Railway server IP'da ham ishlashi mumkin.
# tv_embedded qo'llab-quvvatlash cheklovlari uchun saqlandi.

_YOUTUBE_PLAYER_CLIENTS: list[str] = [
    "ios",          # ✅ PO token kerak emas — eng ishonchli
    "android",      # ✅ PO token kerak emas
    "mweb",         # ✅ Mobil veb
    "tv_embedded",  # ✅ Ayrim cheklangan videolar uchun
]

# Har bir client urinishi uchun maksimal vaqt (soniyada)
_CLIENT_TIMEOUT_SEC = 60

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
    # ios/android/mweb clientlar HLS stream beradi — "best[height<=X]" ishonchli ishlaydi.
    # bestvideo+bestaudio — agar alohida stream mavjud bo'lsa (web client uchun).
    # best — oxirgi fallback, istalgan format.
    (
        "720p",
        (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[ext=mp4][height<=720]"
            "/best[height<=720]"
            "/bestvideo+bestaudio"
            "/best"
        ),
    ),
    (
        "480p",
        (
            "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]"
            "/bestvideo[height<=480]+bestaudio[ext=m4a]"
            "/bestvideo[height<=480]+bestaudio"
            "/best[ext=mp4][height<=480]"
            "/best[height<=480]"
            "/bestvideo+bestaudio"
            "/best"
        ),
    ),
    (
        "360p",
        (
            "bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]"
            "/bestvideo[height<=360]+bestaudio[ext=m4a]"
            "/bestvideo[height<=360]+bestaudio"
            "/best[ext=mp4][height<=360]"
            "/best[height<=360]"
            "/bestvideo+bestaudio"
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
    "po token",
    "proof of origin",
    "this video is unavailable",
    "playback on other websites",
    "sabotaged",
)

_PROXY_ERROR_KEYWORDS = (
    "proxy",
    "proxyerror",
    "tunnel connection failed",
    "socks",
    "cannot connect to proxy",
    "unable to connect to proxy",
    "connection refused",
    "proxy authentication",
    "407",
    "503",
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
    """Xato turinini aniqlaydi: 'bot' | 'permanent' | 'player' | 'proxy' | 'temp'"""
    lower = msg.lower()
    if any(kw in lower for kw in _BOT_DETECTION_KEYWORDS):
        return "bot"
    if any(kw in lower for kw in _PERMANENT_KEYWORDS):
        return "permanent"
    if any(kw in lower for kw in _PLAYER_RESPONSE_KEYWORDS):
        return "player"
    if any(kw in lower for kw in _PROXY_ERROR_KEYWORDS):
        return "proxy"
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


def _build_opts(client: str, proxy: str | None = None) -> dict:
    """
    Berilgan player_client uchun to'liq yt-dlp sozlamalarini qaytaradi.

    Optimizatsiya (tezlik uchun):
      - sleep_interval = 0  (kutish o'chirilgan)
      - retries = 3         (tez muvaffaqiyatsiz bo'lish)
      - socket_timeout = 30 (60 o'rniga)
    """
    opts: dict = {
        # ── Umumiy ──────────────────────────────────────────
        "quiet":                True,
        "no_warnings":          True,
        "socket_timeout":       30,    # 60 o'rniga — tez timeout
        "nocheckcertificate":   True,
        "geo_bypass":           True,
        "extract_flat":         False,
        "skip_download":        False,
        "prefer_free_formats":  False,  # MP4 ni WebM dan afzal ko'r

        # ── Retry (kamaytirildi — tez muvaffaqiyatsiz bo'lish) ──
        "retries":              3,     # 5 o'rniga
        "fragment_retries":     3,     # 10 o'rniga
        "file_access_retries":  2,
        "extractor_retries":    2,

        # ── Sleep O'CHIRILGAN (kutishni kamaytirish) ─────────
        # Railway server IP bloklanganida kutish befoyda — tez o'tish yaxshi.
        "sleep_interval":           0,
        "max_sleep_interval":       0,
        "sleep_interval_requests":  0,

        # ── Brauzer sarlavhalari ──────────────────────────────
        "http_headers": _HEADERS,

        # ── YouTube player_client ─────────────────────────────
        "extractor_args": {
            "youtube": {
                "player_client": [client],
            }
        },
    }

    # Cookies qo'shish
    opts.update(_cookie_opts())

    # Proxy qo'shish (agar berilgan bo'lsa)
    if proxy:
        opts["proxy"] = proxy
        logger.info(f"Using proxy: {proxy}")

    return opts


# ──────────────────────────────────────────────────────────
# Ichki per-client loop funksiyalari
# ──────────────────────────────────────────────────────────

async def _video_per_client(url: str, fmt: str, proxy: str | None) -> str:
    """
    Per-client loop: YouTube player_client'larni navbatma-navbat sinaydi.
    Har bir urinish uchun maksimal _CLIENT_TIMEOUT_SEC soniya timeout.

    Ko'taradi:
      YouTubeAuthError       — bot taniqlash
      PermanentDownloadError — video mavjud emas/himoyalangan
      YouTubePlayerError     — barcha client'lar muvaffaqiyatsiz
      RuntimeError("PROXY_FAILED:...") — proxy xatosi
      Exception              — boshqa vaqtinchalik xato
    """
    platform   = detect_platform(url)
    is_youtube = platform == "youtube"
    clients    = _YOUTUBE_PLAYER_CLIENTS if is_youtube else ["default"]

    last_error:      str = "Noma'lum xato"
    last_error_type: str = "temp"
    player_errors:   int = 0

    for idx, client in enumerate(clients):
        fid    = uuid.uuid4().hex[:10]
        prefix = f"vid_{fid}"
        tpl    = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

        opts = _build_opts(client, proxy=proxy)
        opts.update({
            "format":              fmt,
            "outtmpl":             tpl,
            "merge_output_format": "mp4",
        })

        proxy_tag = " [proxy]" if proxy else ""
        logger.info(
            f"[YouTube:{client}{proxy_tag}] urinish {idx + 1}/{len(clients)}: {url[:55]}..."
            if is_youtube else f"[{client}{proxy_tag}] Yuklab olinmoqda: {url[:55]}..."
        )

        # Default argument trick — closure xatosidan himoya
        result: dict = {"path": None, "error": None, "error_type": "temp"}

        def _run(_o=opts, _r=result) -> None:
            try:
                with yt_dlp.YoutubeDL(_o) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        _r["error"] = "Kontent ma'lumoti olinmadi"
                        return
                    raw  = ydl.prepare_filename(info)
                    base = os.path.splitext(raw)[0]
                    mp4  = base + ".mp4"
                    if os.path.exists(mp4):
                        _r["path"] = mp4
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                _r["error"]      = msg
                _r["error_type"] = _classify_error(msg)
            except Exception as exc:
                _r["error"]      = str(exc)
                _r["error_type"] = _classify_error(str(exc))

        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=float(_CLIENT_TIMEOUT_SEC),
            )
        except asyncio.TimeoutError:
            _cleanup_prefix(DOWNLOAD_PATH, prefix)
            logger.warning(
                f"[{client}] {_CLIENT_TIMEOUT_SEC}s timeout — keyingi clientga o'tamiz"
            )
            last_error      = f"Timeout: {client} {_CLIENT_TIMEOUT_SEC}s da javob bermadi"
            last_error_type = "temp"
            continue

        # ── Muvaffaqiyatli yuklab olindi ──────────────────
        if result["path"] and os.path.exists(result["path"]):
            logger.info(f"[{client}] ✅ Muvaffaqiyatli: {result['path']}")
            return result["path"]

        found = _find_output_file(DOWNLOAD_PATH, prefix)
        if found:
            logger.info(f"[{client}] ✅ Fayl topildi: {found}")
            return found

        error      = result["error"] or "Noma'lum xato"
        error_type = result["error_type"]

        _cleanup_prefix(DOWNLOAD_PATH, prefix)

        # ── Xato turlari ──────────────────────────────────
        if error_type == "bot":
            logger.warning(f"[{client}] Bot taniqlash xatosi → darhol to'xtatildi")
            raise YouTubeAuthError(error)

        if error_type == "permanent":
            logger.warning(f"[{client}] Permanent xato: {error[:80]}")
            raise PermanentDownloadError(error)

        if error_type == "proxy":
            logger.warning(f"[{client}] Proxy xatosi: {error[:80]}")
            raise RuntimeError(f"PROXY_FAILED:{error}")

        if error_type == "player":
            player_errors += 1
            next_msg = (
                "Keyingi clientga o'tamiz."
                if idx + 1 < len(clients)
                else "Barcha clientlar tugadi."
            )
            logger.warning(f"[{client}] Player response xatosi ({player_errors}). {next_msg}")
        else:
            logger.warning(f"[{client}] Xato ({error_type}): {error[:80]}")

        last_error      = error
        last_error_type = error_type

    # ── Barcha clientlar muvaffaqiyatsiz ──────────────────
    if is_youtube:
        raise YouTubePlayerError(
            f"Barcha player_client urinishlari muvaffaqiyatsiz: {last_error[:100]}"
        )
    raise Exception(last_error)


async def _audio_per_client(url: str, bitrate_num: str, proxy: str | None) -> str:
    """
    Per-client loop: audio uchun. Timeout va proxy fallback bilan.
    """
    platform   = detect_platform(url)
    is_youtube = platform == "youtube"
    clients    = _YOUTUBE_PLAYER_CLIENTS if is_youtube else ["default"]

    last_error:      str = "Noma'lum xato"
    last_error_type: str = "temp"
    player_errors:   int = 0

    for idx, client in enumerate(clients):
        fid    = uuid.uuid4().hex[:10]
        prefix = f"aud_{fid}"
        tpl    = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

        opts = _build_opts(client, proxy=proxy)
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

        proxy_tag = " [proxy]" if proxy else ""
        logger.info(
            f"[YouTube:{client}{proxy_tag}] audio {idx + 1}/{len(clients)}"
            if is_youtube else f"[{client}{proxy_tag}] Audio yuklab olinmoqda..."
        )

        result: dict = {"path": None, "error": None, "error_type": "temp"}

        def _run(_o=opts, _r=result) -> None:
            try:
                with yt_dlp.YoutubeDL(_o) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        _r["error"] = "Kontent ma'lumoti olinmadi"
                        return
                    raw  = ydl.prepare_filename(info)
                    base = os.path.splitext(raw)[0]
                    mp3  = base + ".mp3"
                    if os.path.exists(mp3):
                        _r["path"] = mp3
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                _r["error"]      = msg
                _r["error_type"] = _classify_error(msg)
            except Exception as exc:
                _r["error"]      = str(exc)
                _r["error_type"] = _classify_error(str(exc))

        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=float(_CLIENT_TIMEOUT_SEC),
            )
        except asyncio.TimeoutError:
            _cleanup_prefix(DOWNLOAD_PATH, prefix)
            logger.warning(f"[{client}] {_CLIENT_TIMEOUT_SEC}s timeout — keyingi client")
            last_error      = f"Timeout: {client} javob bermadi"
            last_error_type = "temp"
            continue

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
        if error_type == "proxy":
            logger.warning(f"[{client}] Proxy xatosi: {error[:80]}")
            raise RuntimeError(f"PROXY_FAILED:{error}")
        if error_type == "player":
            player_errors += 1
            logger.warning(f"[{client}] Player response xatosi → keyingisiga o'tamiz")
        else:
            logger.warning(f"[{client}] Xato ({error_type}): {error[:80]}")

        last_error      = error
        last_error_type = error_type

    if is_youtube:
        raise YouTubePlayerError(last_error)
    raise Exception(last_error)


# ──────────────────────────────────────────────────────────
# Public API — proxy fallback wrapper
# ──────────────────────────────────────────────────────────

async def download_raw_video(url: str, fmt: str) -> str:
    """
    Berilgan yt-dlp format matni bilan xom MP4 yuklab oladi.

    PROXY_URL o'rnatilgan bo'lsa avval proxy bilan urinadi.
    Proxy xatosi bo'lsa → proxysiz qayta urinadi.
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

    if PROXY_URL:
        try:
            return await _video_per_client(url, fmt, proxy=PROXY_URL)
        except RuntimeError as exc:
            if str(exc).startswith("PROXY_FAILED:"):
                logger.warning(f"Proxy failed ({PROXY_URL}), retrying without proxy")
                return await _video_per_client(url, fmt, proxy=None)
            raise

    return await _video_per_client(url, fmt, proxy=None)


async def download_audio(url: str) -> str:
    """
    URL dan ovoz ajratib, MP3 128 kbps formatida yuklab oladi.
    PROXY_URL o'rnatilgan bo'lsa proxy bilan, xato bo'lsa proxysiz.
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    bitrate_num = AUDIO_BITRATE.rstrip("kK")

    if PROXY_URL:
        try:
            return await _audio_per_client(url, bitrate_num, proxy=PROXY_URL)
        except RuntimeError as exc:
            if str(exc).startswith("PROXY_FAILED:"):
                logger.warning(f"Proxy failed ({PROXY_URL}), retrying without proxy")
                return await _audio_per_client(url, bitrate_num, proxy=None)
            raise

    return await _audio_per_client(url, bitrate_num, proxy=None)


# ──────────────────────────────────────────────────────────
# Moslik qatlamasi
# ──────────────────────────────────────────────────────────

async def download_media(url: str, media_type: str = "video") -> str:
    """Eski kod uchun moslik. Yangi kod download_raw_video / download_audio ishlatsin."""
    if media_type == "audio":
        return await download_audio(url)
    return await download_raw_video(url, QUALITY_PRESETS[0][1])
