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
    COOKIES_PATH,
    DOWNLOAD_PATH,
    PROXY_URL,
    YOUTUBE_COOKIES_ENABLED,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Invidious instance'lari (bot detection yo'q)
# ──────────────────────────────────────────────────────────

INVIDIOUS_INSTANCES: list[str] = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.private.coffee",
    "https://inv.tux.pizza",
    "https://invidious.incogniweb.net",
    "https://inv.bp.projectsegfau.lt",
    "https://invidious.no-logs.com",
    "https://yt.drgnz.club",
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

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Invidious] {instance}: HTTP {resp.status}")
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

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
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

    # ── YouTube: Invidious birinchi ──────────────────────
    if platform == "youtube":
        video_id = _extract_youtube_id(url)
        if video_id:
            max_height = _fmt_to_max_height(fmt)

            # 1-urinish: Invidious API (to'g'ridan-to'g'ri stream yuklab olish)
            try:
                return await _invidious_video(video_id, max_height)
            except YouTubePlayerError:
                logger.info("[Invidious API] muvaffaqiyatsiz → yt-dlp+Invidious URL...")
            except Exception as exc:
                logger.warning(f"[Invidious API] xato: {exc}")

            # 2-urinish: yt-dlp + Invidious URL (yt-dlp Invidious extractorini ishlatadi)
            for inv_instance in INVIDIOUS_INSTANCES:
                inv_url = f"{inv_instance}/watch?v={video_id}"
                fid     = uuid.uuid4().hex[:10]
                prefix  = f"vid_{fid}"
                tpl     = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")

                ytdlp_opts = {
                    "quiet": True, "no_warnings": True,
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                    "outtmpl": tpl, "merge_output_format": "mp4",
                    "socket_timeout": 30, "retries": 2,
                    "nocheckcertificate": True,
                    "sleep_interval": 0, "max_sleep_interval": 0,
                }

                result: dict = {"path": None, "error": None}
                logger.info(f"[Invidious yt-dlp] {inv_instance} → {video_id}")

                def _run_inv(_o=ytdlp_opts, _r=result, _u=inv_url) -> None:
                    try:
                        with yt_dlp.YoutubeDL(_o) as ydl:
                            info = ydl.extract_info(_u, download=True)
                            if info:
                                raw  = ydl.prepare_filename(info)
                                base = os.path.splitext(raw)[0]
                                mp4  = base + ".mp4"
                                if os.path.exists(mp4):
                                    _r["path"] = mp4
                    except Exception as exc:
                        _r["error"] = str(exc)

                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, _run_inv),
                        timeout=90.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"[Invidious yt-dlp] {inv_instance}: 90s timeout")
                    _cleanup_prefix(DOWNLOAD_PATH, prefix)
                    continue

                if result["path"] and os.path.exists(result["path"]):
                    logger.info(f"✅ [Invidious yt-dlp] {inv_instance}")
                    return result["path"]

                found = _find_output_file(DOWNLOAD_PATH, prefix)
                if found:
                    logger.info(f"✅ [Invidious yt-dlp] {inv_instance} (found)")
                    return found

                if result["error"]:
                    logger.debug(
                        f"[Invidious yt-dlp] {inv_instance}: {result['error'][:80]}"
                    )
                _cleanup_prefix(DOWNLOAD_PATH, prefix)

            logger.info("[Invidious yt-dlp] hammasi muvaffaqiyatsiz → yt-dlp YouTube URL...")

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

    # ── YouTube: Invidious birinchi ──────────────────────
    if platform == "youtube":
        video_id = _extract_youtube_id(url)
        if video_id:
            try:
                return await _invidious_audio(video_id, f"{bitrate_num}k")
            except YouTubePlayerError:
                logger.info("[Invidious audio] muvaffaqiyatsiz → yt-dlp sinab ko'rilmoqda...")
            except Exception as exc:
                logger.warning(
                    f"[Invidious audio] kutilmagan xato: {exc} → yt-dlp..."
                )

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
