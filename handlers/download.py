"""
handlers/download.py
────────────────────
Video va MP3 yuklab olish:
  - FSM orqali URL kutiladi
  - yt-dlp yuklab oladi
  - Video uchun watermark qo'shiladi (WATERMARK_ENABLED=true bo'lsa)
  - MP3 uchun watermark QOSHILMAYDI
  - Anti-spam cooldown
"""

import logging
import os
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import (
    CHANNEL_URL,
    COOLDOWN_SECONDS,
    MAX_FILE_SIZE,
    WATERMARK_ENABLED,
)
from keyboards.inline import cancel_keyboard, main_menu_keyboard, subscription_keyboard
from utils.checker import check_subscription
from utils.database import increment_downloads, log_download
from utils.downloader import detect_platform, download_media
from utils.helpers import cleanup_file, format_size, is_valid_url, user_friendly_error
from utils.watermark import apply_watermark

logger = logging.getLogger(__name__)
router = Router()

_PLATFORM_EMOJI = {"youtube": "🔴", "instagram": "📸", "tiktok": "🎵"}

# ── Anti-spam: user_id → so'nggi so'rov vaqti ────────────
_cooldowns: dict[int, float] = {}


class DLState(StatesGroup):
    waiting_video_url = State()
    waiting_audio_url = State()


def _cooldown_remaining(user_id: int) -> int:
    ts = _cooldowns.get(user_id)
    if ts is None:
        return 0
    return max(0, int(COOLDOWN_SECONDS - (time.monotonic() - ts)))


def _set_cooldown(user_id: int) -> None:
    _cooldowns[user_id] = time.monotonic()


# ──────────────────────────────────────────────────────────
# Callback: 🎥 Video yuklash
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "download_video")
async def cb_download_video(callback: CallbackQuery, state: FSMContext, bot) -> None:
    uid = callback.from_user.id

    if not await check_subscription(bot, uid):
        await callback.message.edit_text(
            f"🔒 Botdan foydalanish uchun kanalga obuna bo'ling:\n{CHANNEL_URL}",
            reply_markup=subscription_keyboard(),
        )
        await callback.answer()
        return

    rem = _cooldown_remaining(uid)
    if rem:
        await callback.answer(f"⏳ Iltimos {rem} soniya kuting!", show_alert=True)
        return

    await state.set_state(DLState.waiting_video_url)
    await callback.message.edit_text(
        "🎥 <b>Video Yuklash</b>\n\n"
        "Quyidagi platformalar linkini yuboring:\n\n"
        "├ 🔴  YouTube (video, Shorts)\n"
        "├ 📸  Instagram (Reels, Post)\n"
        "└ 🎵  TikTok\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📎 <b>Linkni yuboring...</b>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: 🎵 MP3 yuklash
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "download_audio")
async def cb_download_audio(callback: CallbackQuery, state: FSMContext, bot) -> None:
    uid = callback.from_user.id

    if not await check_subscription(bot, uid):
        await callback.message.edit_text(
            f"🔒 Botdan foydalanish uchun kanalga obuna bo'ling:\n{CHANNEL_URL}",
            reply_markup=subscription_keyboard(),
        )
        await callback.answer()
        return

    rem = _cooldown_remaining(uid)
    if rem:
        await callback.answer(f"⏳ Iltimos {rem} soniya kuting!", show_alert=True)
        return

    await state.set_state(DLState.waiting_audio_url)
    await callback.message.edit_text(
        "🎵 <b>MP3 Yuklash</b>\n\n"
        "Quyidagi platformalar linkini yuboring:\n\n"
        "├ 🔴  YouTube\n"
        "├ 📸  Instagram\n"
        "└ 🎵  TikTok\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📎 <b>Linkni yuboring...</b>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: ❌ Bekor qilish
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Bekor qilindi.\n\nAsosiy menyu:",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("Bekor qilindi")


# ──────────────────────────────────────────────────────────
# Holat: Video URL qayta ishlash
# ──────────────────────────────────────────────────────────

@router.message(DLState.waiting_video_url)
async def process_video_url(message: Message, state: FSMContext) -> None:
    url = message.text.strip() if message.text else ""

    if not is_valid_url(url):
        await message.answer(
            "❌ <b>Noto'g'ri link!</b>\n\nTo'g'ri URL manzil yuboring.",
            reply_markup=cancel_keyboard(),
        )
        return

    platform = detect_platform(url)
    if platform == "unknown":
        await message.answer(
            "❌ <b>Qo'llab-quvvatlanmaydigan platforma!</b>\n\n"
            "Faqat YouTube, Instagram va TikTok linklariga ruxsat berilgan.",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.clear()
    _set_cooldown(message.from_user.id)

    emoji = _PLATFORM_EMOJI[platform]
    wm_label = "  ·  🎨 watermark" if WATERMARK_ENABLED else ""
    status = await message.answer(
        f"⏳ <b>Yuklab olinmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4{wm_label}\n\n"
        f"🔄 Iltimos kuting..."
    )

    raw_path:  str | None = None
    wm_path:   str | None = None
    send_path: str | None = None

    try:
        # 1. Yuklab olish
        raw_path = await download_media(url, media_type="video")

        # 2. Watermark qo'shish (faqat video, MP3 ga qo'shilmaydi)
        if WATERMARK_ENABLED:
            await status.edit_text(
                f"🎨 <b>Watermark qo'shilmoqda...</b>\n\n"
                f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4\n\n"
                f"🔄 Iltimos kuting..."
            )
            try:
                wm_path   = await apply_watermark(raw_path)
                send_path = wm_path
            except Exception as wm_err:
                # Watermark xatolik bo'lsa — originalni yuboramiz
                logger.warning(f"Watermark xatolik, original yuboriladi: {wm_err}")
                send_path = raw_path
        else:
            send_path = raw_path

        # 3. Hajm tekshiruvi
        size = os.path.getsize(send_path)
        if size > MAX_FILE_SIZE:
            await status.edit_text(
                "❌ <b>Fayl juda katta!</b>\n\n"
                "Telegram 50 MB dan katta fayllarni qabul qilmaydi.\n"
                "Boshqa sifatda urinib ko'ring.",
                reply_markup=main_menu_keyboard(),
            )
            return

        # 4. Yuborish
        await status.edit_text(f"📤 <b>Yuborilmoqda...</b>  ({format_size(size)})")

        wm_badge = "  ·  🎨 Watermark" if WATERMARK_ENABLED else ""
        await message.answer_video(
            video=FSInputFile(send_path),
            caption=(
                f"✅ <b>Muvaffaqiyatli yuklandi!</b>\n\n"
                f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n"
                f"📦 Hajm: <b>{format_size(size)}</b>{wm_badge}\n\n"
                f"🤖 @laminox"
            ),
            reply_markup=main_menu_keyboard(),
        )
        await status.delete()

        # 5. DB logi
        await increment_downloads(message.from_user.id)
        await log_download(message.from_user.id, platform, "video", url)

    except Exception as exc:
        logger.error(f"Video yuklashda xatolik [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        # Ikkala vaqtinchalik fayl ham o'chiriladi
        if raw_path:
            await cleanup_file(raw_path)
        if wm_path and wm_path != raw_path:
            await cleanup_file(wm_path)


# ──────────────────────────────────────────────────────────
# Holat: Audio URL qayta ishlash
# ──────────────────────────────────────────────────────────

@router.message(DLState.waiting_audio_url)
async def process_audio_url(message: Message, state: FSMContext) -> None:
    url = message.text.strip() if message.text else ""

    if not is_valid_url(url):
        await message.answer(
            "❌ <b>Noto'g'ri link!</b>\n\nTo'g'ri URL manzil yuboring.",
            reply_markup=cancel_keyboard(),
        )
        return

    platform = detect_platform(url)
    if platform == "unknown":
        await message.answer(
            "❌ <b>Qo'llab-quvvatlanmaydigan platforma!</b>\n\n"
            "Faqat YouTube, Instagram va TikTok linklariga ruxsat berilgan.",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.clear()
    _set_cooldown(message.from_user.id)

    emoji = _PLATFORM_EMOJI[platform]
    status = await message.answer(
        f"⏳ <b>Audio ajratilmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  🎵 MP3\n\n"
        f"🔄 Iltimos kuting..."
    )

    file_path: str | None = None
    try:
        # MP3 uchun watermark QOSHILMAYDI
        file_path = await download_media(url, media_type="audio")

        size = os.path.getsize(file_path)
        if size > MAX_FILE_SIZE:
            await status.edit_text(
                "❌ <b>Fayl juda katta!</b>\n\n"
                "Telegram 50 MB chekloviga ega.",
                reply_markup=main_menu_keyboard(),
            )
            return

        await status.edit_text(f"📤 <b>Yuborilmoqda...</b>  ({format_size(size)})")

        await message.answer_audio(
            audio=FSInputFile(file_path),
            caption=(
                f"✅ <b>MP3 muvaffaqiyatli yuklandi!</b>\n\n"
                f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n"
                f"📦 Hajm: <b>{format_size(size)}</b>\n\n"
                f"🤖 @laminox"
            ),
            reply_markup=main_menu_keyboard(),
        )
        await status.delete()

        await increment_downloads(message.from_user.id)
        await log_download(message.from_user.id, platform, "audio", url)

    except Exception as exc:
        logger.error(f"Audio yuklashda xatolik [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        if file_path:
            await cleanup_file(file_path)
