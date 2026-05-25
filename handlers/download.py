import logging
import os
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import CHANNEL_URL, COOLDOWN_SECONDS, MAX_FILE_SIZE
from keyboards.inline import cancel_keyboard, main_menu_keyboard, subscription_keyboard
from utils.checker import check_subscription
from utils.database import increment_downloads, log_download
from utils.downloader import detect_platform, download_media
from utils.helpers import cleanup_file, format_size, is_valid_url, user_friendly_error

logger = logging.getLogger(__name__)
router = Router()

_PLATFORM_EMOJI = {"youtube": "🔴", "instagram": "📸", "tiktok": "🎵"}

# user_id -> last request timestamp
_cooldowns: dict[int, float] = {}


class DLState(StatesGroup):
    waiting_video_url = State()
    waiting_audio_url = State()


def _get_cooldown_remaining(user_id: int) -> int:
    ts = _cooldowns.get(user_id)
    if ts is None:
        return 0
    remaining = COOLDOWN_SECONDS - (time.monotonic() - ts)
    return max(0, int(remaining))


def _set_cooldown(user_id: int) -> None:
    _cooldowns[user_id] = time.monotonic()


# ──────────────────────────────────────────────────────────
# Callback: Video
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

    remaining = _get_cooldown_remaining(uid)
    if remaining:
        await callback.answer(f"⏳ Iltimos {remaining} soniya kuting!", show_alert=True)
        return

    await state.set_state(DLState.waiting_video_url)
    await callback.message.edit_text(
        text=(
            "🎥 <b>Video Yuklash</b>\n\n"
            "Quyidagi platformalar linkini yuboring:\n\n"
            "├ 🔴  YouTube (video, Shorts)\n"
            "├ 📸  Instagram (Reels, Post)\n"
            "└ 🎵  TikTok\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📎 <b>Linkni yuboring...</b>"
        ),
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: Audio
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

    remaining = _get_cooldown_remaining(uid)
    if remaining:
        await callback.answer(f"⏳ Iltimos {remaining} soniya kuting!", show_alert=True)
        return

    await state.set_state(DLState.waiting_audio_url)
    await callback.message.edit_text(
        text=(
            "🎵 <b>MP3 Yuklash</b>\n\n"
            "Quyidagi platformalar linkini yuboring:\n\n"
            "├ 🔴  YouTube\n"
            "├ 📸  Instagram\n"
            "└ 🎵  TikTok\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📎 <b>Linkni yuboring...</b>"
        ),
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: Cancel
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
# State: Process video URL
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
    status_msg = await message.answer(
        f"⏳ <b>Yuklab olinmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4\n\n"
        f"🔄 Iltimos kuting..."
    )

    file_path: str | None = None
    try:
        file_path = await download_media(url, media_type="video")

        size = os.path.getsize(file_path)
        if size > MAX_FILE_SIZE:
            await status_msg.edit_text(
                "❌ <b>Fayl juda katta!</b>\n\n"
                "Telegram 50 MB dan katta fayllarni qabul qilmaydi.\n"
                "Boshqa sifatda urinib ko'ring.",
                reply_markup=main_menu_keyboard(),
            )
            return

        await status_msg.edit_text(f"📤 <b>Yuborilmoqda...</b>  ({format_size(size)})")

        await message.answer_video(
            video=FSInputFile(file_path),
            caption=(
                f"✅ <b>Muvaffaqiyatli yuklandi!</b>\n\n"
                f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n"
                f"📦 Hajm: <b>{format_size(size)}</b>\n\n"
                f"🤖 @MediaLoaderProBot"
            ),
            reply_markup=main_menu_keyboard(),
        )
        await status_msg.delete()

        await increment_downloads(message.from_user.id)
        await log_download(message.from_user.id, platform, "video", url)

    except Exception as exc:
        logger.error(f"Video yuklashda xatolik [{platform}]: {exc}")
        await status_msg.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        if file_path:
            await cleanup_file(file_path)


# ──────────────────────────────────────────────────────────
# State: Process audio URL
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
    status_msg = await message.answer(
        f"⏳ <b>Audio ajratilmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  🎵 MP3\n\n"
        f"🔄 Iltimos kuting..."
    )

    file_path: str | None = None
    try:
        file_path = await download_media(url, media_type="audio")

        size = os.path.getsize(file_path)
        if size > MAX_FILE_SIZE:
            await status_msg.edit_text(
                "❌ <b>Fayl juda katta!</b>\n\n"
                "Telegram 50 MB chekloviga ega.",
                reply_markup=main_menu_keyboard(),
            )
            return

        await status_msg.edit_text(f"📤 <b>Yuborilmoqda...</b>  ({format_size(size)})")

        await message.answer_audio(
            audio=FSInputFile(file_path),
            caption=(
                f"✅ <b>MP3 muvaffaqiyatli yuklandi!</b>\n\n"
                f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n"
                f"📦 Hajm: <b>{format_size(size)}</b>\n\n"
                f"🤖 @MediaLoaderProBot"
            ),
            reply_markup=main_menu_keyboard(),
        )
        await status_msg.delete()

        await increment_downloads(message.from_user.id)
        await log_download(message.from_user.id, platform, "audio", url)

    except Exception as exc:
        logger.error(f"Audio yuklashda xatolik [{platform}]: {exc}")
        await status_msg.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        if file_path:
            await cleanup_file(file_path)
