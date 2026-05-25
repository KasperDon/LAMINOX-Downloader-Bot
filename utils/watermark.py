"""
utils/watermark.py
──────────────────
FFmpeg orqali MP4 videoga pastki o'ng burchakda matn watermark qo'shadi.
MP3 fayllar uchun bu modul chaqirilmaydi.

Sozlamalar config.py → WATERMARK_ENABLED, WATERMARK_TEXT
"""

import asyncio
import logging
import os
import uuid

from config import DOWNLOAD_PATH, WATERMARK_TEXT

logger = logging.getLogger(__name__)


def _escape_drawtext(text: str) -> str:
    """FFmpeg drawtext filter uchun maxsus belgilarni ekranlash."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


async def apply_watermark(input_path: str) -> str:
    """
    input_path  → yuklab olingan original MP4
    return      → watermark qo'shilgan yangi MP4 yo'li

    Xatolik bo'lsa Exception ko'taradi —
    handler uni ushlab, original faylni yuboradi.
    """
    fid = uuid.uuid4().hex[:8]
    output_path = os.path.join(DOWNLOAD_PATH, f"wm_{fid}.mp4")

    safe_text = _escape_drawtext(WATERMARK_TEXT)

    # drawtext filter:
    #   pastki o'ng burchak (w-tw-15 : h-th-15)
    #   oq yozuv, 70% shaffoflik
    #   qora yariq shaffof fon (professional ko'rinish)
    vf = (
        f"drawtext=text='{safe_text}'"
        f":fontcolor=white@0.85"
        f":fontsize=24"
        f":x=w-tw-15"
        f":y=h-th-15"
        f":box=1"
        f":boxcolor=black@0.45"
        f":boxborderw=7"
    )

    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-vf", vf,
        "-codec:v", "libx264",
        "-preset", "veryfast",   # tez encoding
        "-crf", "23",            # sifat (18=yuqori, 28=past)
        "-codec:a", "copy",      # audio o'zgarmaydi
        "-movflags", "+faststart",
        "-y",                    # mavjud faylni ustiga yoz
        output_path,
    ]

    logger.info(f"Watermark qo'shilmoqda: {os.path.basename(input_path)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode(errors="ignore")[-300:]
        logger.error(f"FFmpeg watermark xatolik (kod {proc.returncode}): {err}")
        raise RuntimeError(f"FFmpeg xatolik: {err}")

    if not os.path.exists(output_path):
        raise RuntimeError("Watermark fayli yaratilmadi")

    logger.info(f"Watermark tayyor: {os.path.basename(output_path)}")
    return output_path
