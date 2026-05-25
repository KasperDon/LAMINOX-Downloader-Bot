"""
utils/helpers.py
────────────────
Umumiy yordamchi funksiyalar:
  is_valid_url()       — URL tekshiruvi
  format_size()        — baytlarni odam o'qiy oladigan formatga o'girish
  cleanup_file()       — vaqtinchalik faylni o'chirish
  user_friendly_error() — yt-dlp xato xabarini foydalanuvchi tiliga o'girish
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

_URL_RE = re.compile(
    r"^https?://"
    r"(?:[A-Z0-9](?:[A-Z0-9\-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}"
    r"(?::\d+)?(?:/[^\s]*)?$",
    re.IGNORECASE,
)


def is_valid_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def format_size(size_bytes: int) -> str:
    """Baytlarni odam o'qiy oladigan formatga o'giradi."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


async def cleanup_file(path: str) -> None:
    """Vaqtinchalik faylni (va thumbnail'ini) o'chiradi."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
        # yt-dlp ba'zan .jpg thumbnail qoldiradi
        thumb = os.path.splitext(path)[0] + ".jpg" if path else None
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
    except Exception as exc:
        logger.warning(f"Fayl o'chirishda xatolik ({path}): {exc}")


def user_friendly_error(error: str) -> str:
    """
    yt-dlp / FFmpeg xato xabarini foydalanuvchi tushunadigan
    Uzbek tilidagi xabarga o'giradi.
    """
    e = error.lower()

    # ── YouTube bot taniqlash ──────────────────────────────
    if any(kw in e for kw in (
        "sign in to confirm", "not a bot", "confirm you're not a bot",
        "bot detection", "please sign in",
    )):
        return (
            "⚠️ <b>YouTube vaqtincha ishlamayapti!</b>\n\n"
            "YouTube bot taniqlash tizimini ishga tushirdi.\n"
            "Admin <code>cookies.txt</code> faylini serverga "
            "joylashtirishi kerak.\n\n"
            "🔄 Keyinroq qayta urinib ko'ring."
        )

    # ── Maxfiy / login kerak ──────────────────────────────
    if any(kw in e for kw in ("private", "login required", "members only")):
        return "🔒 Bu kontent <b>maxfiy (private)</b>. Faqat ochiq kontentni yuklab olish mumkin."

    # ── Topilmadi ─────────────────────────────────────────
    if any(kw in e for kw in ("not found", "404", "does not exist", "no such")):
        return "🔍 Kontent <b>topilmadi</b>. Link to'g'ri ekanligini tekshiring."

    # ── Timeout ───────────────────────────────────────────
    if any(kw in e for kw in ("timeout", "timed out", "connection reset", "read timeout")):
        return "⏱ So'rov vaqti tugadi. Iltimos qayta urinib ko'ring."

    # ── Mualliflik huquqi ─────────────────────────────────
    if any(kw in e for kw in ("copyright", "drm", "content id")):
        return "©️ Bu kontent <b>mualliflik huquqi</b> bilan himoyalangan."

    # ── O'chirilgan / mavjud emas ─────────────────────────
    if any(kw in e for kw in ("unavailable", "removed", "deleted", "video unavailable")):
        return "⚠️ Kontent mavjud emas yoki o'chirilgan."

    # ── Geo-bloklangan ────────────────────────────────────
    if any(kw in e for kw in ("geo", "not available in your country", "region")):
        return "🌍 Bu kontent sizning <b>hududingizda mavjud emas</b> (geo-block)."

    # ── Yosh cheklovi ─────────────────────────────────────
    if any(kw in e for kw in ("age", "18+", "adult", "confirm your age")):
        return "🔞 Bu kontent <b>yosh cheklovi</b> bilan himoyalangan."

    # ── Fayl juda katta ───────────────────────────────────
    if any(kw in e for kw in ("too large", "filesize", "size limit")):
        return "📦 Fayl juda katta. Telegram 50 MB ga ruxsat beradi."

    # ── Format topilmadi ──────────────────────────────────
    if any(kw in e for kw in ("format", "no video", "no streams", "no media")):
        return "🎬 Mos format topilmadi. Boshqa link sinab ko'ring."

    # ── FFmpeg xatoligi ───────────────────────────────────
    if "ffmpeg" in e:
        return "⚙️ Video qayta ishlashda xatolik. Boshqa link sinab ko'ring."

    # ── Umumiy ────────────────────────────────────────────
    return "⚠️ Yuklab olishda xatolik yuz berdi. Iltimos qayta urinib ko'ring."
