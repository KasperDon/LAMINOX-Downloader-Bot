"""
handlers/download.py
────────────────────
Video va MP3 yuklab olish — 2 xil rejim:

  REJIM 1 — Tugma orqali (FSM):
    Foydalanuvchi "🎥 Video" yoki "🎵 MP3" tugmasini bosadi
    → Bot link yuborishni so'raydi (DLState.waiting_video_url / waiting_audio_url)
    → Foydalanuvchi link yuboradi → yuklab olish boshlanadi

  REJIM 2 — To'g'ridan-to'g'ri link (StateFilter(None)):
    Foydalanuvchi hech qanday holatsiz link yuboradi
    → Bot platformani aniqlaydi → format so'raydi (DLState.waiting_format)
    → Foydalanuvchi "🎥 Video" yoki "🎵 MP3" tugmasini bosadi → yuklab olish

Xavfsizlik:
  - Obuna tekshiruvi har so'rovda
  - Anti-spam cooldown
  - Vaqtinchalik fayllar har doim o'chiriladi (finally blok)
"""

import logging
import os
import time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import CHANNEL_URL, COOLDOWN_SECONDS, MAX_FILE_SIZE, WATERMARK_ENABLED
from keyboards.inline import (
    cancel_keyboard,
    format_choice_keyboard,
    main_menu_keyboard,
    subscription_keyboard,
)
from utils.checker import check_subscription
from utils.database import increment_downloads, log_download
from utils.downloader import detect_platform, download_media
from utils.helpers import cleanup_file, format_size, is_valid_url, user_friendly_error
from utils.watermark import apply_watermark

logger = logging.getLogger(__name__)
router = Router()

_PLATFORM_EMOJI: dict[str, str] = {
    "youtube":   "🔴",
    "instagram": "📸",
    "tiktok":    "🎵",
}

# Anti-spam: user_id → oxirgi so'rov vaqti (monotonic)
_cooldowns: dict[int, float] = {}


# ── FSM holatlari ─────────────────────────────────────────

class DLState(StatesGroup):
    waiting_video_url = State()   # "🎥 Video" tugmasi bosildi, link kutilmoqda
    waiting_audio_url = State()   # "🎵 MP3"  tugmasi bosildi, link kutilmoqda
    waiting_format    = State()   # To'g'ridan-to'g'ri link keldi, format kutilmoqda


# ── Cooldown yordamchilari ────────────────────────────────

def _cooldown_remaining(user_id: int) -> int:
    ts = _cooldowns.get(user_id)
    if ts is None:
        return 0
    return max(0, int(COOLDOWN_SECONDS - (time.monotonic() - ts)))


def _set_cooldown(user_id: int) -> None:
    _cooldowns[user_id] = time.monotonic()


# ══════════════════════════════════════════════════════════
# YADRO: yuklab olish va yuborish funksiyalari
# (har ikkala rejimdan ham chaqiriladi)
# ══════════════════════════════════════════════════════════

async def _do_video(ref: Message, user_id: int, url: str, platform: str) -> None:
    """Video yuklab olib, watermark qo'shib, yuboradi."""
    emoji    = _PLATFORM_EMOJI.get(platform, "🌐")
    wm_label = "  ·  🎨 watermark" if WATERMARK_ENABLED else ""

    status = await ref.answer(
        f"⏳ <b>Yuklab olinmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4{wm_label}\n\n"
        f"🔄 Iltimos kuting..."
    )

    raw_path: str | None = None
    wm_path:  str | None = None

    try:
        raw_path = await download_media(url, media_type="video")

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
                logger.warning(f"Watermark xatolik, original yuboriladi: {wm_err}")
                send_path = raw_path
        else:
            send_path = raw_path

        size = os.path.getsize(send_path)
        if size > MAX_FILE_SIZE:
            await status.edit_text(
                "❌ <b>Fayl juda katta!</b>\n\n"
                "Telegram 50 MB dan katta fayllarni qabul qilmaydi.\n"
                "Boshqa sifatda urinib ko'ring.",
                reply_markup=main_menu_keyboard(),
            )
            return

        await status.edit_text(f"📤 <b>Yuborilmoqda...</b>  ({format_size(size)})")

        wm_badge = "  ·  🎨 Watermark" if WATERMARK_ENABLED else ""
        await ref.answer_video(
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

        await increment_downloads(user_id)
        await log_download(user_id, platform, "video", url)

    except Exception as exc:
        logger.error(f"Video xatolik [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        if raw_path:
            await cleanup_file(raw_path)
        if wm_path and wm_path != raw_path:
            await cleanup_file(wm_path)


async def _do_audio(ref: Message, user_id: int, url: str, platform: str) -> None:
    """Audio ajratib, MP3 formatda yuboradi. Watermark qo'shilmaydi."""
    emoji  = _PLATFORM_EMOJI.get(platform, "🌐")
    status = await ref.answer(
        f"⏳ <b>Audio ajratilmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  🎵 MP3\n\n"
        f"🔄 Iltimos kuting..."
    )

    file_path: str | None = None
    try:
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

        await ref.answer_audio(
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

        await increment_downloads(user_id)
        await log_download(user_id, platform, "audio", url)

    except Exception as exc:
        logger.error(f"Audio xatolik [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        if file_path:
            await cleanup_file(file_path)


# ══════════════════════════════════════════════════════════
# REJIM 2: To'g'ridan-to'g'ri link → format so'rash
# StateFilter(None) = foydalanuvchi hech qanday holatda emas
# ══════════════════════════════════════════════════════════

@router.message(StateFilter(None), F.text.regexp(r"https?://\S+"))
async def handle_url_direct(message: Message, state: FSMContext, bot) -> None:
    """
    Foydalanuvchi tugma bosmay to'g'ridan-to'g'ri link yuborsa.
    Platformani aniqlaydi va format tanlashni so'raydi.
    """
    url = message.text.strip()
    uid = message.from_user.id

    # Obuna tekshiruvi
    if not await check_subscription(bot, uid):
        await message.answer(
            f"🔒 Botdan foydalanish uchun kanalga obuna bo'ling:\n{CHANNEL_URL}",
            reply_markup=subscription_keyboard(),
        )
        return

    # Cooldown
    rem = _cooldown_remaining(uid)
    if rem:
        await message.answer(
            f"⏳ Iltimos <b>{rem}</b> soniya kuting!",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Platforma aniqlash
    platform = detect_platform(url)
    if platform == "unknown":
        await message.answer(
            "❌ <b>Qo'llab-quvvatlanmaydigan platforma!</b>\n\n"
            "Faqat YouTube, Instagram va TikTok linklariga ruxsat berilgan.\n\n"
            "Qo'llab-quvvatlanadigan:\n"
            "├ 🔴  youtube.com / youtu.be\n"
            "├ 📸  instagram.com\n"
            "└ 🎵  tiktok.com",
            reply_markup=main_menu_keyboard(),
        )
        return

    # URL va platformani FSM data'ga saqlash, format so'rash
    await state.set_state(DLState.waiting_format)
    await state.update_data(url=url, platform=platform)

    emoji = _PLATFORM_EMOJI[platform]
    await message.answer(
        f"🔗 <b>Link aniqlandi!</b>\n\n"
        f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Qaysi formatda yuklab olishni xohlaysiz?",
        reply_markup=format_choice_keyboard(),
    )


# ── Format tanlash callbacklari ───────────────────────────

@router.callback_query(DLState.waiting_format, F.data == "fmt_video")
async def cb_fmt_video(callback: CallbackQuery, state: FSMContext) -> None:
    data     = await state.get_data()
    url      = data.get("url", "")
    platform = data.get("platform", "unknown")
    await state.clear()
    _set_cooldown(callback.from_user.id)
    # Tugmani olib tashla
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()
    await _do_video(callback.message, callback.from_user.id, url, platform)


@router.callback_query(DLState.waiting_format, F.data == "fmt_audio")
async def cb_fmt_audio(callback: CallbackQuery, state: FSMContext) -> None:
    data     = await state.get_data()
    url      = data.get("url", "")
    platform = data.get("platform", "unknown")
    await state.clear()
    _set_cooldown(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()
    await _do_audio(callback.message, callback.from_user.id, url, platform)


# waiting_format holatida boshqa matn kelsa (URL emas)
@router.message(DLState.waiting_format)
async def handle_text_while_choosing(message: Message) -> None:
    await message.answer(
        "👆 Yuqoridagi tugmalardan birini bosing:\n\n"
        "🎥 Video (MP4)  yoki  🎵 Audio (MP3)\n\n"
        "Bekor qilish uchun ❌ tugmasini bosing.",
    )


# ══════════════════════════════════════════════════════════
# REJIM 1: Tugma → FSM holat → link kutish
# ══════════════════════════════════════════════════════════

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


# ── Bekor qilish ──────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Bekor qilindi.\n\nAsosiy menyu:",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("Bekor qilindi")


# ── FSM holat: Video URL qayta ishlash ───────────────────

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
    await _do_video(message, message.from_user.id, url, platform)


# ── FSM holat: Audio URL qayta ishlash ───────────────────

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
    await _do_audio(message, message.from_user.id, url, platform)
