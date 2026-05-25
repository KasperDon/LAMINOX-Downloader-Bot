"""
utils/watermark.py
──────────────────
FFmpeg orqali MP4 videoni H.264 (CRF 28) bilan siqadi va
ixtiyoriy ravishda pastki o'ng burchakka matn watermark qo'shadi.

Asosiy funksiya:
  process_video(input_path, watermark=True) → output_path

Bir o'tishda (single-pass) ham siqish ham watermark:
  - Codec:   libx264  CRF 28  preset fast
  - Audio:   AAC  128 kbps
  - Flags:   +faststart  (veb/Telegram uchun optimallashtirilgan)
  - Watermark: pastki o'ng burchak, oq yozuv, qora yariq shaffof fon
"""

import asyncio
import logging
import os
import uuid

from config import DOWNLOAD_PATH, VIDEO_AUDIO_BITRATE, VIDEO_CRF, WATERMARK_TEXT

logger = logging.getLogger(__name__)


def _escape_drawtext(text: str) -> str:
    """FFmpeg drawtext filter uchun maxsus belgilarni ekranlash."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'",  "\\'")
        .replace(":",  "\\:")
        .replace("%",  "\\%")
    )


async def process_video(input_path: str, watermark: bool = True) -> str:
    """
    input_path  → original MP4 (yt-dlp tomonidan yuklangan)
    watermark   → True: WATERMARK_TEXT matnini qo'shadi
    return      → siqilgan (va ixtiyoriy watermark qo'shilgan) MP4 yo'li

    Xatolik bo'lsa RuntimeError ko'taradi.
    """
    fid         = uuid.uuid4().hex[:8]
    output_path = os.path.join(DOWNLOAD_PATH, f"out_{fid}.mp4")

    cmd = ["ffmpeg", "-i", input_path]

    if watermark:
        safe_text = _escape_drawtext(WATERMARK_TEXT)
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
        cmd += ["-vf", vf]

    cmd += [
        "-codec:v",   "libx264",
        "-preset",    "fast",          # veryfast'dan sifatliroq, tez
        "-crf",       str(VIDEO_CRF),  # 28 = yaxshi siqish, 18 = yuqori sifat
        "-codec:a",   "aac",
        "-b:a",       VIDEO_AUDIO_BITRATE,  # 128k
        "-movflags",  "+faststart",    # Telegram stream uchun
        "-y",                          # mavjud faylni ustiga yoz
        output_path,
    ]

    label = f"[CRF {VIDEO_CRF}{'+ watermark' if watermark else ''}]"
    logger.info(f"FFmpeg {label}: {os.path.basename(input_path)} → {os.path.basename(output_path)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode(errors="ignore")[-400:]
        logger.error(f"FFmpeg xatolik (kod {proc.returncode}): {err}")
        raise RuntimeError(f"FFmpeg xatolik: {err}")

    if not os.path.exists(output_path):
        raise RuntimeError("FFmpeg chiqdi faylini yaratmadi")

    out_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"FFmpeg tayyor {label}: {out_mb:.1f} MB")
    return output_path


# ── Moslik qatlamasi ──────────────────────────────────────

async def apply_watermark(input_path: str) -> str:
    """Eski import'lar uchun. Yangi kod process_video() ishlatsin."""
    return await process_video(input_path, watermark=True)
