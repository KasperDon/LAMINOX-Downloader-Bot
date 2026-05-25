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
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


async def cleanup_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
            thumb = os.path.splitext(path)[0] + ".jpg"
            if os.path.exists(thumb):
                os.remove(thumb)
    except Exception as e:
        logger.warning(f"Fayl o'chirishda xatolik ({path}): {e}")


def user_friendly_error(error: str) -> str:
    e = error.lower()
    if "private" in e or "login required" in e:
        return "🔒 Bu kontent <b>maxfiy (private)</b>. Faqat ochiq kontentni yuklab olish mumkin."
    if "not found" in e or "404" in e or "does not exist" in e:
        return "🔍 Kontent <b>topilmadi</b>. Link to'g'ri ekanligini tekshiring."
    if "timeout" in e or "timed out" in e:
        return "⏱ So'rov vaqti tugadi. Iltimos qayta urinib ko'ring."
    if "copyright" in e or "drm" in e:
        return "©️ Bu kontent <b>mualliflik huquqi</b> bilan himoyalangan."
    if "unavailable" in e or "removed" in e or "deleted" in e:
        return "⚠️ Kontent mavjud emas yoki o'chirilgan."
    if "too large" in e or "filesize" in e:
        return "📦 Fayl juda katta. Telegram 50MB ga ruxsat beradi."
    if "format" in e or "no video" in e:
        return "🎬 Mos format topilmadi. Boshqa link sinab ko'ring."
    return "⚠️ Yuklab olishda xatolik yuz berdi. Iltimos qayta urinib ko'ring."
