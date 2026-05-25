"""
utils/downloader.py
───────────────────
Media yuklash strategiyasi:

  YouTube:
    1. Invidious API  → metadata Railway IP'ga bog'liq emas, bot detection yo'q
    2. yt-dlp         → Invidious muvaffaqiyatsiz bo'lsa fallback

  Instagram / TikTok:
    yt-dlp (proxy bilan yoki proxysiz)

Invidious haqida:
  Invidious — YouTube'ning ochiq alternativ frontend'i.
  /api/v1/videos/{id} stream URL'larini qaytaradi.
  Railway IP bilan bog'liq muammo yo'q — Invidious o'z serveridan so'raydi.

Istisnolar:
  PermanentDownloadError — video mavjud emas / geo-blocked / copyright
  YouTubeAuthError       — YouTube login talab qiladi
  YouTubePlayerError     — barcha urinishlar muvaffaqiyatsiz
"""

import asyncio
import logging
import os
import re
import uuid
from typing import Optional

import aiohttp
import yt_dlp

from config import (
    AUDIO_BITRATE,
    COBALT_API_KEY,
    COOKIES_PATH,
    DOWNLOAD_PATH,
    PROXY_URL,
    YOUTUBE_COOKIES_ENABLED,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Invidious instance'lari (bot detection yo'q)
# ──────────────────────────────────────────────────────────

# api.invidious.io/instances.json?sort_by=health dan olingan (health > 0.8)
INVIDIOUS_INSTANCES: list[str] = [
    "https://inv.nadeko.net",           # Chile         – health 0.99
    "https://invidious.nerdvpn.de",     # Germany       – health 0.97
    "https://yt.chocolatemoo53.com",    # US            – health 0.95
    "https://invidious.f5.si",          # EU            – health 0.92
    "https://inv.thepixora.com",        # EU            – health 0.88
    "https://invidious.private.coffee", # Austria       – fallback
]

# Piped API instance'lari (teampiped.github.io/Piped-Frontend/Instances)
PIPED_INSTANCES: list[str] = [
    "https://api.piped.private.coffee",
    "https://pipedapi.kavin.rocks",
    "https://api.piped.yt",
    "https://api.piped.privacydev.net",
    "https://piped-api.garudalinux.org",
]

# Cobalt API instance'lari (github.com/imputnet/cobalt)
# Cobalt YouTube'ni o'z serverlaridan yuklaydi — Railway IP bloklangan bo'lsa ham ishlaydi.
# MUHIM: proxy ishlatilmaydi — Cobalt Railway IPni bloklaydi, proxy 502 qaytarishi mumkin.
# ssl=False: community instance sertifikatlari muddati o'tgan/o'z-imzolangan bo'lishi mumkin.
# API versiyasi:
#   v10 (yangi): POST /           — {"url","videoQuality","downloadMode"}
#   v7  (eski):  POST /api/json   — {"url","vQuality","isAudioOnly"}
COBALT_INSTANCES: list[str] = [
    "https://cobalt.api.timelessnesses.me",  # v7 (404 qaytardi → /api/json sinab ko'riladi)
    "https://cobalt.drgns.space",            # v7 (405 qaytardi → /api/json sinab ko'riladi)
    "https://cobaltapi.void.cat",            # community
    "https://co.wuk.sh",                     # community
    "https://api.cobalt.tools",              # rasmiy v10; COBALT_API_KEY talab qiladi
]

# Progressive stream itag → balandlik (audio+video birlashtirilgan, merge kerak emas)
_PROGRESSIVE_ITAGS: dict[int, int] = {22: 720, 59: 480, 18: 360, 17: 144}

# Adaptive video itag → balandlik (faqat video, audio alohida)
_ADAPTIVE_VIDEO_ITAGS: dict[int, int] = {
    137: 1080, 248: 1080,   # 1080p (mp4 / webm)
    136: 720,  247: 720,    # 720p
    135: 480,  244: 480,    # 480p
    134: 360,  243: 360,    # 360p
    133: 240,  242: 240,    # 240p
    160: 144,  278: 144,    # 144p
    # AV1 / VP9
    394: 144, 395: 240, 396: 360, 397: 480, 398: 720, 399: 1080,
}

# ──────────────────────────────────────────────────────────
# YouTube player_client (yt-dlp fallback uchun)
# ──────────────────────────────────────────────────────────

_YOUTUBE_PLAYER_CLIENTS: list[str] = [
    "ios",           # PO token kerak emas
    "android",       # PO token kerak emas
    "web_creator",   # YouTube Studio client
    "android_vr",    # VR client
    "mweb",          # Mobil veb
    "tv_embedded",   # TV embedded
]

_CLIENT_TIMEOUT_SEC = 60

# ──────────────────────────────────────────────────────────
# Brauzer sarlavhalari
# ──────────────────────────────────────────────────────────

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.youtube.com/",
    "Origin":          "https://www.youtube.com",
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
# Cascading sifat darajalari (yt-dlp uchun)
# ──────────────────────────────────────────────────────────

QUALITY_PRESETS: list[tuple[str, str]] = [
    (
        "720p",
        (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[ext=mp4][height<=720]"
            "/best[height<=720]"
            "/bestvideo+bestaudio/best"
        ),
    ),
    (
        "480p",
        (
            "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]"
            "/bestvideo[height<=480]+bestaudio"
            "/best[ext=mp4][height<=480]"
            "/best[height<=480]"
            "/bestvideo+bestaudio/best"
        ),
    ),
    (
        "360p",
        (
            "bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]"
            "/bestvideo[height<=360]+bestaudio"
            "/best[ext=mp4][height<=360]"
            "/best[height<=360]"
            "/bestvideo+bestaudio/best"
        ),
    ),
]

# ──────────────────────────────────────────────────────────
# Xato klassifikatsiya
# ──────────────────────────────────────────────────────────

_BOT_DETECTION_KEYWORDS = (
    "sign in to confirm", "confirm you're not a bot",
    "not a bot", "bot detection", "please sign in",
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
    "player response", "no player response",
    "could not find match for", "unable to extract",
    "nsig extraction failed", "sign in to watch",
    "po token", "proof of origin",
    "playback on other websites", "sabotaged",
)

_PROXY_ERROR_KEYWORDS = (
    "proxy", "proxyerror", "tunnel connection failed", "socks",
    "cannot connect to proxy", "unable to connect to proxy",
    "connection refused", "proxy authentication", "407", "503",
)


# ──────────────────────────────────────────────────────────
# Istisnolar
# ──────────────────────────────────────────────────────────

class PermanentDownloadError(Exception):
    """Video mavjud emas, himoyalangan yoki geo-blocked."""


class YouTubeAuthError(Exception):
    """YouTube bot taniqlash — cookies kerak."""


class YouTubePlayerError(Exception):
    """Barcha urinishlar muvaffaqiyatsiz."""


# ──────────────────────────────────────────────────────────
# Yordamchi funksiyalar
# ──────────────────────────────────────────────────────────

def _classify_error(msg: str) -> str:
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
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix):
                return os.path.join(directory, name)
    except OSError:
        pass
    return None


def _cleanup_prefix(directory: str, prefix: str) -> None:
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
    if YOUTUBE_COOKIES_ENABLED and os.path.exists(COOKIES_PATH):
        return {"cookiefile": COOKIES_PATH}
    return {}


def _build_opts(client: str, proxy: str | None = None) -> dict:
    opts: dict = {
        "quiet":                True,
        "no_warnings":          True,
        "socket_timeout":       30,
        "nocheckcertificate":   True,
        "geo_bypass":           True,
        "extract_flat":         False,
        "skip_download":        False,
        "prefer_free_formats":  False,
        "retries":              3,
        "fragment_retries":     3,
        "file_access_retries":  2,
        "extractor_retries":    2,
        "sleep_interval":       0,
        "max_sleep_interval":   0,
        "sleep_interval_requests": 0,
        "http_headers":         _HEADERS,
        "extractor_args": {
            "youtube": {"player_client": [client]},
        },
    }
    opts.update(_cookie_opts())
    if proxy:
        opts["proxy"] = proxy
        logger.info(f"Using proxy: {proxy}")
    return opts


def _extract_youtube_id(url: str) -> str | None:
    """YouTube URL'dan 11 belgili video ID'ni ajratadi."""
    m = re.search(
        r"(?:v=|youtu\.be/|shorts/|embed/|watch\?.*v=)([A-Za-z0-9_-]{11})",
        url,
    )
    return m.group(1) if m else None


def _fmt_to_max_height(fmt: str) -> int:
    """yt-dlp format matni'dan maksimal balandlikni ajratadi."""
    m = re.search(r"height<=(\d+)", fmt)
    return int(m.group(1)) if m else 720


# ──────────────────────────────────────────────────────────
# Invidious: YouTube video yuklab olish
# ──────────────────────────────────────────────────────────

def _safe_itag(s: dict) -> int:
    """itag qiymatini xavfsiz int'ga aylantiradi (Invidious string qaytaradi)."""
    try:
        return int(s.get("itag") or 0)
    except (TypeError, ValueError):
        return 0


def _safe_int(val) -> int:
    """Istalgan qiymatni xavfsiz int'ga aylantiradi."""
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


async def _download_stream(session: aiohttp.ClientSession, url: str, out: str) -> bool:
    """URL'dan faylni yuklab oladi. Muvaffaqiyatli bo'lsa True qaytaradi."""
    if url.startswith("/"):
        return False
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status not in (200, 206):
                logger.debug(f"stream HTTP {resp.status}: {url[:60]}")
                return False
            with open(out, "wb") as f:
                async for chunk in resp.content.iter_chunked(512 * 1024):
                    f.write(chunk)
        size = os.path.getsize(out) if os.path.exists(out) else 0
        return size > 10_000
    except Exception as e:
        logger.debug(f"stream yuklab olish xatosi: {e}")
        return False


async def _invidious_video(video_id: str, max_height: int) -> str:
    """
    Invidious API orqali YouTube video yuklab oladi.

    1. formatStreams (progressive, audio+video birlashtirilgan) sinab ko'riladi
    2. Agar yo'q → adaptiveFormats (alohida video+audio, FFmpeg bilan birlashtiradi)

    Muhim tuzatish: itag Invidious'da STRING ("22") — int'ga aylantiramiz.
    """
    for instance in INVIDIOUS_INSTANCES:
        try:
            api_url = (
                f"{instance}/api/v1/videos/{video_id}"
                "?fields=formatStreams,adaptiveFormats,title"
            )
            logger.info(f"[Invidious] {instance} → {video_id} (max {max_height}p)")

            # PROXY_URL orqali so'rov — Railway IP bloki aylanib o'tiladi
            _api_proxy = PROXY_URL or None
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(api_url, proxy=_api_proxy) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Invidious] {instance}: HTTP {resp.status}")
                        continue
                    ct = resp.headers.get("Content-Type", "")
                    if "json" not in ct.lower():
                        logger.warning(
                            f"[Invidious] {instance}: JSON kutildi, lekin '{ct}' keldi"
                        )
                        continue
                    data = await resp.json()

            # ── API javobini tekshirish ──────────────────────────────────
            prog_streams = data.get("formatStreams", [])
            adaptive     = data.get("adaptiveFormats", [])

            # Xato JSON bo'lsa (masalan: {"error": "Video unavailable"})
            if "error" in data:
                logger.warning(
                    f"[Invidious] {instance}: API xatosi → {data['error']}"
                )
                continue

            logger.info(
                f"[Invidious] {instance}: "
                f"formatStreams={len(prog_streams)}, "
                f"adaptiveFormats={len(adaptive)}"
            )

            # ── 1. Progressive stream (audio+video birlashtirilgan) ──────
            best_prog, best_prog_h = None, 0
            for s in prog_streams:
                h = _PROGRESSIVE_ITAGS.get(_safe_itag(s), 0)
                if 0 < h <= max_height and h > best_prog_h:
                    best_prog, best_prog_h = s, h

            if best_prog:
                url_prog = best_prog.get("url", "")
                if url_prog.startswith("/"):
                    url_prog = instance + url_prog
                if url_prog:
                    fid = uuid.uuid4().hex[:10]
                    out = os.path.join(DOWNLOAD_PATH, f"vid_{fid}.mp4")
                    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
                    logger.info(
                        f"[Invidious] progressive {best_prog_h}p yuklanmoqda..."
                    )
                    dl_sess = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=300, sock_read=120)
                    )
                    async with dl_sess as session:
                        ok = await _download_stream(session, url_prog, out)
                    if ok:
                        size = os.path.getsize(out)
                        logger.info(f"✅ [Invidious] progressive {size//1024} KB")
                        return out
                    try:
                        os.remove(out)
                    except Exception:
                        pass
            else:
                logger.debug(
                    f"[Invidious] {instance}: formatStreams bo'sh yoki mos kelmaди"
                )

            # ── 2. Adaptive stream (video + audio alohida, FFmpeg merge) ──

            # Eng yaxshi video stream
            best_vid, best_vid_h = None, 0
            for s in adaptive:
                if "video" not in s.get("type", ""):
                    continue
                h = _ADAPTIVE_VIDEO_ITAGS.get(_safe_itag(s), 0)
                if 0 < h <= max_height and h > best_vid_h:
                    best_vid, best_vid_h = s, h

            # Eng yaxshi audio stream (m4a birinchi)
            audio_streams = [
                s for s in adaptive
                if "audio" in s.get("type", "")
            ]
            # m4a/mp4 audio'ni afzal ko'ramiz (mp4 bilan birlashtirish osonroq)
            audio_streams.sort(
                key=lambda s: (
                    "mp4" in s.get("type", ""),
                    _safe_int(s.get("bitrate", 0)),
                ),
                reverse=True,
            )
            best_aud = audio_streams[0] if audio_streams else None

            if not best_vid or not best_aud:
                logger.warning(
                    f"[Invidious] {instance}: adaptive stream topilmadi "
                    f"(video={'ok' if best_vid else 'yo\'q'}, "
                    f"audio={'ok' if best_aud else 'yo\'q'})"
                )
                continue

            vid_url = best_vid.get("url", "")
            aud_url = best_aud.get("url", "")
            if vid_url.startswith("/"):
                vid_url = instance + vid_url
            if aud_url.startswith("/"):
                aud_url = instance + aud_url

            fid     = uuid.uuid4().hex[:10]
            vid_tmp = os.path.join(DOWNLOAD_PATH, f"vid_{fid}_v.mp4")
            aud_tmp = os.path.join(DOWNLOAD_PATH, f"vid_{fid}_a.m4a")
            out     = os.path.join(DOWNLOAD_PATH, f"vid_{fid}.mp4")
            os.makedirs(DOWNLOAD_PATH, exist_ok=True)

            logger.info(
                f"[Invidious] adaptive {best_vid_h}p yuklanmoqda "
                f"({instance})..."
            )

            dl_timeout = aiohttp.ClientTimeout(total=300, sock_read=120)
            async with aiohttp.ClientSession(timeout=dl_timeout) as session:
                vid_ok = await _download_stream(session, vid_url, vid_tmp)
                aud_ok = await _download_stream(session, aud_url, aud_tmp)

            if not vid_ok or not aud_ok:
                logger.warning(
                    f"[Invidious] adaptive yuklab olish xatosi "
                    f"(video={'ok' if vid_ok else 'fail'}, "
                    f"audio={'ok' if aud_ok else 'fail'})"
                )
                for p in (vid_tmp, aud_tmp):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
                continue

            # FFmpeg bilan birlashtirish
            logger.info("[Invidious] FFmpeg merge qilinmoqda...")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i", vid_tmp, "-i", aud_tmp,
                "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart",
                "-y", out,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("[Invidious] FFmpeg merge timeout")
                for p in (vid_tmp, aud_tmp, out):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
                continue
            finally:
                for p in (vid_tmp, aud_tmp):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

            size = os.path.getsize(out) if os.path.exists(out) else 0
            if size > 50_000:
                logger.info(f"✅ [Invidious] adaptive {size//1024} KB")
                return out

            try:
                os.remove(out)
            except Exception:
                pass

        except asyncio.TimeoutError:
            logger.warning(f"[Invidious] {instance}: timeout")
        except Exception as exc:
            logger.warning(f"[Invidious] {instance}: {type(exc).__name__}: {exc}")

    raise YouTubePlayerError(
        f"Barcha Invidious instance'lar muvaffaqiyatsiz ({video_id})"
    )


# ──────────────────────────────────────────────────────────
# Invidious: YouTube audio yuklab olish
# ──────────────────────────────────────────────────────────

async def _invidious_audio(video_id: str, bitrate: str) -> str:
    """
    Invidious API orqali YouTube audio stream (m4a) yuklab oladi,
    so'ngra FFmpeg bilan MP3'ga convert qiladi.
    """
    for instance in INVIDIOUS_INSTANCES:
        m4a_out: str | None = None
        mp3_out: str | None = None
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}?fields=adaptiveFormats"
            logger.info(f"[Invidious audio] {instance} → {video_id}")

            _api_proxy = PROXY_URL or None
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(api_url, proxy=_api_proxy) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"[Invidious audio] {instance}: HTTP {resp.status}"
                        )
                        continue
                    data = await resp.json()

            audio_streams = [
                f for f in data.get("adaptiveFormats", [])
                if "audio" in f.get("type", "")
            ]
            if not audio_streams:
                logger.debug(f"[Invidious audio] {instance}: audio stream topilmadi")
                continue

            # Eng yuqori bitrate'li audio (bitrate string bo'lishi mumkin)
            audio_streams.sort(
                key=lambda x: _safe_int(x.get("bitrate", 0)), reverse=True
            )
            best = audio_streams[0]

            stream_url = best.get("url", "")
            if not stream_url:
                continue
            if stream_url.startswith("/"):
                stream_url = instance + stream_url

            fid = uuid.uuid4().hex[:10]
            m4a_out = os.path.join(DOWNLOAD_PATH, f"aud_{fid}.m4a")
            mp3_out = os.path.join(DOWNLOAD_PATH, f"aud_{fid}.mp3")
            os.makedirs(DOWNLOAD_PATH, exist_ok=True)

            logger.info(f"[Invidious audio] yuklab olinmoqda ({instance})...")

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300, sock_read=120)
            ) as session:
                async with session.get(
                    stream_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status not in (200, 206):
                        continue
                    with open(m4a_out, "wb") as f:
                        async for chunk in resp.content.iter_chunked(512 * 1024):
                            f.write(chunk)

            if not os.path.exists(m4a_out) or os.path.getsize(m4a_out) < 10_000:
                logger.warning(f"[Invidious audio] Fayl kichik/yo'q")
                if m4a_out and os.path.exists(m4a_out):
                    os.remove(m4a_out)
                continue

            # m4a → mp3 (FFmpeg)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", m4a_out,
                "-vn", "-acodec", "mp3", "-b:a", bitrate,
                "-y", mp3_out,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=120)

            try:
                os.remove(m4a_out)
            except Exception:
                pass

            if os.path.exists(mp3_out) and os.path.getsize(mp3_out) > 10_000:
                logger.info(
                    f"✅ [Invidious audio] {os.path.getsize(mp3_out) // 1024} KB"
                )
                return mp3_out

        except asyncio.TimeoutError:
            logger.warning(f"[Invidious audio] {instance}: timeout")
        except Exception as exc:
            logger.warning(
                f"[Invidious audio] {instance}: {type(exc).__name__}: {exc}"
            )
        finally:
            for path in (m4a_out,):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

    raise YouTubePlayerError(
        f"Barcha Invidious audio instance'lar muvaffaqiyatsiz ({video_id})"
    )


# ──────────────────────────────────────────────────────────
# yt-dlp: per-client loop (Instagram/TikTok + YouTube fallback)
# ──────────────────────────────────────────────────────────

async def _video_per_client(url: str, fmt: str, proxy: str | None) -> str:
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
            f"[yt-dlp:{client}{proxy_tag}] {idx+1}/{len(clients)}: {url[:55]}..."
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
            logger.warning(f"[yt-dlp:{client}] {_CLIENT_TIMEOUT_SEC}s timeout")
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
            logger.warning(f"[yt-dlp:{client}] Proxy xatosi: {error[:80]}")
            raise RuntimeError(f"PROXY_FAILED:{error}")
        if error_type == "player":
            player_errors += 1
            next_msg = (
                "Keyingi clientga o'tamiz."
                if idx + 1 < len(clients)
                else "Barcha clientlar tugadi."
            )
            logger.warning(
                f"[yt-dlp:{client}] Player xatosi ({player_errors}). {next_msg}"
            )
        else:
            logger.warning(f"[yt-dlp:{client}] Xato ({error_type}): {error[:80]}")

        last_error      = error
        last_error_type = error_type

    if is_youtube:
        raise YouTubePlayerError(
            f"yt-dlp barcha clientlar muvaffaqiyatsiz: {last_error[:100]}"
        )
    raise Exception(last_error)


async def _audio_per_client(url: str, bitrate_num: str, proxy: str | None) -> str:
    platform   = detect_platform(url)
    is_youtube = platform == "youtube"
    clients    = _YOUTUBE_PLAYER_CLIENTS if is_youtube else ["default"]

    last_error:      str = "Noma'lum xato"
    last_error_type: str = "temp"

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
            logger.warning(f"[yt-dlp audio:{client}] timeout")
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
            raise RuntimeError(f"PROXY_FAILED:{error}")
        if error_type == "player":
            logger.warning(f"[yt-dlp audio:{client}] player xatosi → keyingisi")

        last_error      = error
        last_error_type = error_type

    if is_youtube:
        raise YouTubePlayerError(last_error)
    raise Exception(last_error)


# ──────────────────────────────────────────────────────────
# Piped API: YouTube video yuklab olish (Invidious alternativa)
# ──────────────────────────────────────────────────────────

def _piped_height(s: dict) -> int:
    """Piped stream ob'ektidan piksel balandligini ajratadi."""
    h = s.get("height", 0)
    if h:
        try:
            return int(h)
        except (TypeError, ValueError):
            pass
    m = re.match(r"(\d+)p", s.get("quality", ""))
    return int(m.group(1)) if m else 0


async def _piped_video(video_id: str, max_height: int) -> str:
    """
    Piped API orqali YouTube video yuklab oladi.

    Piped — Invidious'ga o'xshash, lekin boshqa infratuzilma.
    /streams/{id} endpointi videoStreams + audioStreams qaytaradi.
    Barcha streamlar video-only (adaptive) — FFmpeg merge kerak.
    """
    for instance in PIPED_INSTANCES:
        try:
            api_url = f"{instance}/streams/{video_id}"
            logger.info(f"[Piped] {instance} → {video_id} (max {max_height}p)")

            _api_proxy = PROXY_URL or None
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(api_url, proxy=_api_proxy) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Piped] {instance}: HTTP {resp.status}")
                        continue
                    ct = resp.headers.get("Content-Type", "")
                    if "json" not in ct.lower():
                        logger.warning(
                            f"[Piped] {instance}: JSON kutildi, lekin '{ct}' keldi"
                        )
                        continue
                    data = await resp.json()

            if "error" in data:
                logger.warning(f"[Piped] {instance}: API xatosi → {data['error']}")
                continue

            video_streams = data.get("videoStreams", [])
            audio_streams = data.get("audioStreams", [])
            logger.info(
                f"[Piped] {instance}: "
                f"videoStreams={len(video_streams)}, audioStreams={len(audio_streams)}"
            )

            # ── Eng yaxshi video stream (mp4, ≤ max_height) ──────────────
            mp4_videos = [
                s for s in video_streams
                if "mp4" in s.get("mimeType", "").lower()
                and s.get("videoOnly", True)
                and 0 < _piped_height(s) <= max_height
            ]
            mp4_videos.sort(key=_piped_height, reverse=True)

            if not mp4_videos:
                # Istalgan format bo'lsa ham sinab ko'ramiz
                all_videos = [
                    s for s in video_streams
                    if s.get("videoOnly", True)
                    and 0 < _piped_height(s) <= max_height
                ]
                all_videos.sort(key=_piped_height, reverse=True)
                best_vid = all_videos[0] if all_videos else None
            else:
                best_vid = mp4_videos[0]

            # ── Eng yaxshi audio stream (m4a/mp4 birinchi) ───────────────
            mp4_audio = [
                s for s in audio_streams
                if "mp4" in s.get("mimeType", "").lower()
            ]
            best_aud = (
                mp4_audio[0] if mp4_audio
                else (audio_streams[0] if audio_streams else None)
            )

            if not best_vid or not best_aud:
                v_str = "ok" if best_vid else "yoq"
                a_str = "ok" if best_aud else "yoq"
                logger.warning(
                    f"[Piped] {instance}: stream topilmadi "
                    f"(video={v_str}, audio={a_str})"
                )
                continue

            vid_url = best_vid.get("url", "")
            aud_url = best_aud.get("url", "")
            if not vid_url or not aud_url:
                logger.warning(f"[Piped] {instance}: URL bo'sh")
                continue

            fid     = uuid.uuid4().hex[:10]
            vid_tmp = os.path.join(DOWNLOAD_PATH, f"vid_{fid}_v.mp4")
            aud_tmp = os.path.join(DOWNLOAD_PATH, f"vid_{fid}_a.m4a")
            out     = os.path.join(DOWNLOAD_PATH, f"vid_{fid}.mp4")
            os.makedirs(DOWNLOAD_PATH, exist_ok=True)

            h = _piped_height(best_vid)
            logger.info(f"[Piped] {h}p yuklanmoqda ({instance})...")

            dl_timeout = aiohttp.ClientTimeout(total=300, sock_read=120)
            async with aiohttp.ClientSession(timeout=dl_timeout) as session:
                vid_ok = await _download_stream(session, vid_url, vid_tmp)
                aud_ok = await _download_stream(session, aud_url, aud_tmp)

            if not vid_ok or not aud_ok:
                logger.warning(
                    f"[Piped] stream yuklab olish xatosi "
                    f"(video={'ok' if vid_ok else 'fail'}, "
                    f"audio={'ok' if aud_ok else 'fail'})"
                )
                for p in (vid_tmp, aud_tmp):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
                continue

            # FFmpeg bilan birlashtirish
            logger.info("[Piped] FFmpeg merge qilinmoqda...")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i", vid_tmp, "-i", aud_tmp,
                "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart",
                "-y", out,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("[Piped] FFmpeg timeout")
                for p in (vid_tmp, aud_tmp, out):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
                continue
            finally:
                for p in (vid_tmp, aud_tmp):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

            size = os.path.getsize(out) if os.path.exists(out) else 0
            if size > 50_000:
                logger.info(f"✅ [Piped] {size // 1024} KB")
                return out

            try:
                os.remove(out)
            except Exception:
                pass

        except asyncio.TimeoutError:
            logger.warning(f"[Piped] {instance}: timeout")
        except Exception as exc:
            logger.warning(f"[Piped] {instance}: {type(exc).__name__}: {exc}")

    raise YouTubePlayerError(
        f"Barcha Piped instance'lar muvaffaqiyatsiz ({video_id})"
    )


# ──────────────────────────────────────────────────────────
# Cobalt API: YouTube video / audio yuklab olish
# ──────────────────────────────────────────────────────────

_COBALT_HEADERS = {
    "Accept":       "application/json",
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (compatible; TelegramBot/1.0)",
}

_HEIGHT_TO_COBALT: dict[int, str] = {
    1080: "1080", 720: "720", 480: "480",
    360: "360",  240: "240", 144: "144",
}


def _cobalt_quality(max_height: int) -> str:
    for h in sorted(_HEIGHT_TO_COBALT, reverse=True):
        if max_height >= h:
            return _HEIGHT_TO_COBALT[h]
    return "360"


async def _cobalt_video(video_id: str, max_height: int) -> str:
    """
    Cobalt API orqali YouTube video yuklab oladi.

    Cobalt o'z serverlaridan YouTube stream URL'ni oladi.
    Proxy ISHLATILMAYDI — Cobalt Railway IPni bloklaydi, proxy esa 502 beradi.

    API versiyalari:
      v10 (yangi): POST /         {"url","videoQuality","downloadMode"}
      v7  (eski):  POST /api/json {"url","vQuality","isAudioOnly"}

    Rasmiyl instance (api.cobalt.tools) JWT talab qiladi.
    COBALT_API_KEY o'rnatilsa — Authorization: Api-Key header qo'shiladi.
    """
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    quality     = _cobalt_quality(max_height)

    payload_v10 = {"url": youtube_url, "videoQuality": quality, "downloadMode": "auto"}
    payload_v7  = {"url": youtube_url, "vQuality":     quality, "isAudioOnly": False}

    for instance in COBALT_INSTANCES:
        data: dict | None = None
        try:
            # JWT auth faqat rasmiy instance uchun
            headers = dict(_COBALT_HEADERS)
            if COBALT_API_KEY and "api.cobalt.tools" in instance:
                headers["Authorization"] = f"Api-Key {COBALT_API_KEY}"
                logger.info(f"[Cobalt] {instance} → {video_id} ({quality}p) [JWT]")
            else:
                logger.info(f"[Cobalt] {instance} → {video_id} ({quality}p)")

            # v10 → fallback v7
            for ep, pl in [("/", payload_v10), ("/api/json", payload_v7)]:
                api_url = instance.rstrip("/") + ep
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=20)
                    ) as session:
                        async with session.post(
                            api_url, json=pl, headers=headers, ssl=False
                        ) as resp:
                            if resp.status in (404, 405):
                                # Eski versiya — boshqa endpoint sinab ko'riladi
                                logger.debug(
                                    f"[Cobalt] {instance}{ep}: {resp.status} "
                                    f"→ v7 endpoint sinab ko'rilmoqda"
                                )
                                continue
                            if resp.status == 429:
                                logger.warning(f"[Cobalt] {instance}: rate limit")
                                break
                            if resp.status != 200:
                                body = ""
                                try:
                                    body = (await resp.text())[:150]
                                except Exception:
                                    pass
                                logger.warning(
                                    f"[Cobalt] {instance}{ep}: "
                                    f"HTTP {resp.status} — {body}"
                                )
                                break  # Boshqa endpoint sinab ko'rishning ma'nosi yo'q
                            data = await resp.json(content_type=None)
                            break  # Muvaffaqiyatli javob olindi
                except (asyncio.TimeoutError, aiohttp.ClientError) as conn_exc:
                    logger.warning(
                        f"[Cobalt] {instance}{ep}: "
                        f"{type(conn_exc).__name__}: {conn_exc}"
                    )
                    break  # Ulanish xatosi — instance'ni o'tkazib yuboramiz

            if data is None:
                continue

            # Javobni tahlil qilish (v7 va v10 bir xil format)
            status = data.get("status", "")
            if status == "error":
                err_obj = data.get("error", data.get("text", ""))
                code    = (
                    err_obj.get("code", str(err_obj))
                    if isinstance(err_obj, dict)
                    else str(err_obj)
                )
                logger.warning(f"[Cobalt] {instance}: API xatosi → {code}")
                continue

            dl_url = data.get("url")
            if not dl_url:
                logger.warning(
                    f"[Cobalt] {instance}: URL yo'q ({status}) — {str(data)[:100]}"
                )
                continue

            logger.info(f"[Cobalt] {status} → yuklab olinmoqda...")

            fid = uuid.uuid4().hex[:10]
            out = os.path.join(DOWNLOAD_PATH, f"vid_{fid}.mp4")
            os.makedirs(DOWNLOAD_PATH, exist_ok=True)

            dl_timeout = aiohttp.ClientTimeout(total=300, sock_read=120)
            async with aiohttp.ClientSession(timeout=dl_timeout) as session:
                ok = await _download_stream(session, dl_url, out)

            if ok:
                size = os.path.getsize(out)
                logger.info(f"✅ [Cobalt] {size // 1024} KB")
                return out

            try:
                os.remove(out)
            except Exception:
                pass
            logger.warning(f"[Cobalt] {instance}: fayl kichik/bo'sh")

        except asyncio.TimeoutError:
            logger.warning(f"[Cobalt] {instance}: timeout")
        except Exception as exc:
            logger.warning(f"[Cobalt] {instance}: {type(exc).__name__}: {exc}")

    raise YouTubePlayerError(
        f"Barcha Cobalt instance'lar muvaffaqiyatsiz ({video_id})"
    )


async def _cobalt_audio(video_id: str, bitrate: str) -> str:
    """
    Cobalt API orqali YouTube audio yuklab oladi (MP3).
    bitrate: "128k" formatida.
    """
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    bitrate_num = bitrate.rstrip("kK")

    payload_v10 = {
        "url": youtube_url, "downloadMode": "audio",
        "audioFormat": "mp3", "audioBitrate": bitrate_num,
    }
    payload_v7 = {
        "url": youtube_url, "isAudioOnly": True, "aFormat": "mp3",
    }

    for instance in COBALT_INSTANCES:
        data: dict | None = None
        try:
            headers = dict(_COBALT_HEADERS)
            if COBALT_API_KEY and "api.cobalt.tools" in instance:
                headers["Authorization"] = f"Api-Key {COBALT_API_KEY}"
                logger.info(f"[Cobalt audio] {instance} → {video_id} [JWT]")
            else:
                logger.info(f"[Cobalt audio] {instance} → {video_id}")

            for ep, pl in [("/", payload_v10), ("/api/json", payload_v7)]:
                api_url = instance.rstrip("/") + ep
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=20)
                    ) as session:
                        async with session.post(
                            api_url, json=pl, headers=headers, ssl=False
                        ) as resp:
                            if resp.status in (404, 405):
                                logger.debug(
                                    f"[Cobalt audio] {instance}{ep}: {resp.status}"
                                )
                                continue
                            if resp.status == 429:
                                logger.warning(f"[Cobalt audio] {instance}: rate limit")
                                break
                            if resp.status != 200:
                                body = ""
                                try:
                                    body = (await resp.text())[:150]
                                except Exception:
                                    pass
                                logger.warning(
                                    f"[Cobalt audio] {instance}{ep}: "
                                    f"HTTP {resp.status} — {body}"
                                )
                                break
                            data = await resp.json(content_type=None)
                            break
                except (asyncio.TimeoutError, aiohttp.ClientError) as conn_exc:
                    logger.warning(
                        f"[Cobalt audio] {instance}{ep}: "
                        f"{type(conn_exc).__name__}: {conn_exc}"
                    )
                    break

            if data is None:
                continue

            status = data.get("status", "")
            if status == "error":
                err_obj = data.get("error", data.get("text", ""))
                code    = (
                    err_obj.get("code", str(err_obj))
                    if isinstance(err_obj, dict)
                    else str(err_obj)
                )
                logger.warning(f"[Cobalt audio] {instance}: API xatosi → {code}")
                continue

            dl_url = data.get("url")
            if not dl_url:
                logger.warning(
                    f"[Cobalt audio] {instance}: URL yo'q ({status}) — {str(data)[:100]}"
                )
                continue

            logger.info(f"[Cobalt audio] yuklab olinmoqda ({status})...")

            fid = uuid.uuid4().hex[:10]
            out = os.path.join(DOWNLOAD_PATH, f"aud_{fid}.mp3")
            os.makedirs(DOWNLOAD_PATH, exist_ok=True)

            dl_timeout = aiohttp.ClientTimeout(total=300, sock_read=120)
            async with aiohttp.ClientSession(timeout=dl_timeout) as session:
                ok = await _download_stream(session, dl_url, out)

            if ok:
                size = os.path.getsize(out)
                logger.info(f"✅ [Cobalt audio] {size // 1024} KB")
                return out

            try:
                os.remove(out)
            except Exception:
                pass

        except asyncio.TimeoutError:
            logger.warning(f"[Cobalt audio] {instance}: timeout")
        except Exception as exc:
            logger.warning(f"[Cobalt audio] {instance}: {type(exc).__name__}: {exc}")

    raise YouTubePlayerError(
        f"Barcha Cobalt audio instance'lar muvaffaqiyatsiz ({video_id})"
    )


# ──────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────

async def download_raw_video(url: str, fmt: str) -> str:
    """
    Video yuklab oladi.

    YouTube uchun:  Invidious API (asosiy) → yt-dlp (fallback)
    Boshqalar uchun: yt-dlp (proxy bilan yoki proxysiz)
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    platform = detect_platform(url)

    # ── YouTube: Invidious → Piped → yt-dlp ─────────────
    if platform == "youtube":
        video_id = _extract_youtube_id(url)
        if video_id:
            max_height = _fmt_to_max_height(fmt)

            # 1-urinish: Invidious API (to'g'ridan-to'g'ri stream yuklab olish)
            try:
                return await _invidious_video(video_id, max_height)
            except YouTubePlayerError:
                logger.info("[Invidious] muvaffaqiyatsiz → Piped API...")
            except Exception as exc:
                logger.warning(f"[Invidious] xato: {exc} → Piped API...")

            # 2-urinish: Piped API (boshqa infratuzilma, Invidious'dan mustaqil)
            try:
                return await _piped_video(video_id, max_height)
            except YouTubePlayerError:
                logger.info("[Piped] muvaffaqiyatsiz → Cobalt API...")
            except Exception as exc:
                logger.warning(f"[Piped] xato: {exc} → Cobalt API...")

            # 3-urinish: Cobalt API (o'z serverlarida YouTube'dan oladi)
            try:
                return await _cobalt_video(video_id, max_height)
            except YouTubePlayerError:
                logger.info("[Cobalt] muvaffaqiyatsiz → yt-dlp...")
            except Exception as exc:
                logger.warning(f"[Cobalt] xato: {exc} → yt-dlp...")

    # ── yt-dlp (Instagram/TikTok asosiy, YouTube oxirgi fallback) ──
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
    Audio yuklab oladi (MP3 128kbps).

    YouTube uchun:  Invidious API (asosiy) → yt-dlp (fallback)
    Boshqalar uchun: yt-dlp
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    bitrate_num = AUDIO_BITRATE.rstrip("kK")
    platform    = detect_platform(url)

    # ── YouTube: Invidious → Cobalt → yt-dlp ────────────
    if platform == "youtube":
        video_id = _extract_youtube_id(url)
        if video_id:
            try:
                return await _invidious_audio(video_id, f"{bitrate_num}k")
            except YouTubePlayerError:
                logger.info("[Invidious audio] muvaffaqiyatsiz → Cobalt audio...")
            except Exception as exc:
                logger.warning(f"[Invidious audio] xato: {exc} → Cobalt audio...")

            try:
                return await _cobalt_audio(video_id, f"{bitrate_num}k")
            except YouTubePlayerError:
                logger.info("[Cobalt audio] muvaffaqiyatsiz → yt-dlp...")
            except Exception as exc:
                logger.warning(f"[Cobalt audio] xato: {exc} → yt-dlp...")

    # ── yt-dlp fallback ──────────────────────────────────
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
    """Eski kod uchun moslik."""
    if media_type == "audio":
        return await download_audio(url)
    return await download_raw_video(url, QUALITY_PRESETS[0][1])
