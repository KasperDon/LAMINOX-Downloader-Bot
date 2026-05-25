import asyncio
import logging
import os
import uuid
from typing import Optional

import yt_dlp

from config import DOWNLOAD_PATH

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_PLATFORM_MAP = {
    ("youtube.com", "youtu.be"): "youtube",
    ("instagram.com",): "instagram",
    ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"): "tiktok",
}


def detect_platform(url: str) -> str:
    u = url.lower()
    for domains, name in _PLATFORM_MAP.items():
        if any(d in u for d in domains):
            return name
    return "unknown"


def _build_video_opts(template: str) -> dict:
    return {
        "format": (
            "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
            "/best[ext=mp4][height<=1080]"
            "/best[height<=1080]/best"
        ),
        "outtmpl": template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "retries": 3,
        "http_headers": {"User-Agent": _UA},
    }


def _build_audio_opts(template: str) -> dict:
    return {
        "format": "bestaudio/best",
        "outtmpl": template,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "retries": 3,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "http_headers": {"User-Agent": _UA},
    }


def _find_output_file(directory: str, prefix: str) -> Optional[str]:
    """Search for the downloaded file by prefix in the directory."""
    for name in os.listdir(directory):
        if name.startswith(prefix):
            return os.path.join(directory, name)
    return None


async def download_media(url: str, media_type: str = "video") -> str:
    """Download video or audio from the given URL.

    Returns the absolute path to the downloaded file.
    Raises Exception with a descriptive message on failure.
    """
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    fid = uuid.uuid4().hex[:10]

    if media_type == "video":
        prefix = f"vid_{fid}"
        template = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")
        opts = _build_video_opts(template)
        expected_ext = "mp4"
    else:
        prefix = f"aud_{fid}"
        template = os.path.join(DOWNLOAD_PATH, f"{prefix}.%(ext)s")
        opts = _build_audio_opts(template)
        expected_ext = "mp3"

    result: dict = {"path": None, "error": None}

    def _run() -> None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    result["error"] = "Kontent ma'lumoti olinmadi"
                    return
                raw = ydl.prepare_filename(info)
                base = os.path.splitext(raw)[0]
                candidate = base + "." + expected_ext
                if os.path.exists(candidate):
                    result["path"] = candidate
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

    # Fallback: search by prefix (handles edge-case filenames)
    found = _find_output_file(DOWNLOAD_PATH, prefix)
    if found:
        return found

    raise Exception("Fayl yuklab olingandan keyin topilmadi")
