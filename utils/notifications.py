"""
utils/notifications.py
──────────────────────
Admin xabarnoma tizimi.

notify_new_user()  — yangi foydalanuvchi /start bosganida
notify_download()  — har bir video / MP3 yuklab olinganda

Xabarlar faqat config.py → ADMIN_IDS ro'yxatidagi adminlarga yuboriladi.
"""

import logging
from datetime import datetime

from aiogram import Bot

from config import ADMIN_IDS

logger = logging.getLogger(__name__)

_PLATFORM_EMOJI = {"youtube": "🔴", "instagram": "📸", "tiktok": "🎵"}
_TYPE_LABEL     = {"video": "🎥 Video (MP4)", "audio": "🎵 Audio (MP3)"}


async def _send_to_admins(bot: Bot, text: str) -> None:
    """Barcha adminlarga xabar yuboradi. Xatolik bo'lsa log qiladi."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:
            logger.debug(f"Admin {admin_id} ga xabar yuborilmadi: {exc}")


async def notify_new_user(bot: Bot, user) -> None:
    """
    Yangi foydalanuvchi botni birinchi marta ishga tushirganda
    barcha adminlarga xabar yuboradi.
    """
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = f"@{user.username}" if user.username else "—"
    lang     = user.language_code or "noma'lum"

    text = (
        "🆕 <b>Yangi foydalanuvchi!</b>\n\n"
        f"👤 Ism:      <b>{user.full_name}</b>\n"
        f"🔖 Username: <b>{username}</b>\n"
        f"🆔 ID:       <code>{user.id}</code>\n"
        f"🌐 Til:      <b>{lang}</b>\n"
        f"⏰ Vaqt:     <b>{now}</b>\n\n"
        f"▶️ Yangi foydalanuvchi botni ishga tushirdi."
    )

    await _send_to_admins(bot, text)


async def notify_download(
    bot:        Bot,
    user,
    platform:   str,
    media_type: str,
    file_size:  int,
    url:        str,
) -> None:
    """
    Foydalanuvchi video yoki MP3 yuklab olganda
    barcha adminlarga xabar yuboradi.
    """
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = f"@{user.username}" if user.username else "—"
    pe       = _PLATFORM_EMOJI.get(platform, "🌐")
    tl       = _TYPE_LABEL.get(media_type, "📁 Fayl")
    size_mb  = file_size / (1024 * 1024)

    text = (
        f"📥 <b>Yangi yuklab olish!</b>\n\n"
        f"👤 Ism:      <b>{user.full_name}</b>\n"
        f"🔖 Username: <b>{username}</b>\n"
        f"🆔 ID:       <code>{user.id}</code>\n\n"
        f"{pe} Platforma: <b>{platform.capitalize()}</b>\n"
        f"{tl}\n"
        f"📦 Hajm:     <b>{size_mb:.1f} MB</b>\n"
        f"🔗 Link:\n<code>{url}</code>\n\n"
        f"⏰ Vaqt:     <b>{now}</b>"
    )

    await _send_to_admins(bot, text)
