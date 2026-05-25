"""
handlers/download.py
────────────────────
Video va MP3 yuklab olish — 2 xil rejim:

  REJIM 1 — Tugma orqali (FSM):
    Tugma → waiting_video_url / waiting_audio_url → link → yuklab olish

  REJIM 2 — To'g'ridan-to'g'ri link:
    Link → platforma aniqlanadi → format so'raladi → yuklab olish

Video yuklash strategiyasi (50 MB limit):
  1. 720p yuklab olish → FFmpeg CRF-28 siqish (+ ixtiyoriy watermark)
  2. Agar ≤ 50 MB bo'lsa → yuborish ✅
  3. Aks holda 480p bilan qayta urinish
  4. Kerak bo'lsa 360p
  5. Barchasi katta bo'lsa → xatolik

Admin bildirish:
  Har bir muvaffaqiyatli yuklab olishdan keyin adminlarga xabar yuboriladi.
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
from utils.downloader import (
    QUALITY_PRESETS,
    PermanentDownloadError,
    detect_platform,
    download_audio,
    download_raw_video,
)
from utils.helpers import cleanup_file, format_size, is_valid_url, user_friendly_error
from utils.notifications import notify_download
from utils.watermark import process_video

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
    waiting_video_url = State()   # Tugma bosildi, link kutilmoqda
    waiting_audio_url = State()   # Tugma bosildi, link kutilmoqda
    waiting_format    = State()   # Link keldi, format kutilmoqda


# ── Cooldown ──────────────────────────────────────────────

def _cooldown_remaining(user_id: int) -> int:
    ts = _cooldowns.get(user_id)
    return max(0, int(COOLDOWN_SECONDS - (time.monotonic() - ts))) if ts else 0


def _set_cooldown(user_id: int) -> None:
    _cooldowns[user_id] = time.monotonic()


# ══════════════════════════════════════════════════════════
# YADRO: video yuklab olish (cascading quality)
# ══════════════════════════════════════════════════════════

async def _do_video(ref: Message, user, url: str, platform: str) -> None:
    """
    Video yuklab olib, FFmpeg bilan siqib (+ watermark), Telegramga yuboradi.
    Agar 50 MB dan katta bo'lsa, avtomatik pastroq sifatda qayta urinadi.
    """
    emoji    = _PLATFORM_EMOJI.get(platform, "🌐")
    wm_label = "  ·  🎨 watermark" if WATERMARK_ENABLED else ""

    status = await ref.answer(
        f"⏳ <b>Yuklab olinmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4{wm_label}\n\n"
        f"🔄 Iltimos kuting..."
    )

    raw_path: str | None = None   # yt-dlp tomonidan yuklangan xom MP4
    out_path: str | None = None   # FFmpeg tomonidan qayta ishlangan MP4
    send_path: str | None = None  # Telegramga yuborilish uchun fayl
    send_size: int = 0

    try:
        for i, (quality_label, fmt) in enumerate(QUALITY_PRESETS):

            # ── Keyingi urinish uchun xabar ───────────────
            if i > 0:
                await status.edit_text(
                    f"📐 <b>Video hajmi katta, {quality_label}da qayta tayyorlanmoqda...</b>\n\n"
                    f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4\n\n"
                    f"🔄 Iltimos kuting..."
                )
                # Oldingi xom faylni o'chirish
                if raw_path:
                    await cleanup_file(raw_path)
                    raw_path = None

            # ── 1. yt-dlp: xom video yuklab olish ─────────
            try:
                raw_path = await download_raw_video(url, fmt)
            except PermanentDownloadError:
                raise  # Qayta urinish befoyda
            except Exception:
                if i < len(QUALITY_PRESETS) - 1:
                    continue   # Keyingi sifatni sinab ko'r
                raise          # Oxirgi urinish ham muvaffaqiyatsiz

            # ── 2. FFmpeg: siqish (+ ixtiyoriy watermark) ─
            await status.edit_text(
                f"⚙️ <b>{quality_label}: siqilmoqda"
                f"{'  · 🎨 watermark' if WATERMARK_ENABLED else ''}...</b>\n\n"
                f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4\n\n"
                f"🔄 Iltimos kuting..."
            )

            # Oldingi FFmpeg natijasini tozalash
            if out_path and out_path != raw_path:
                await cleanup_file(out_path)
            out_path = None

            try:
                out_path = await process_video(raw_path, watermark=WATERMARK_ENABLED)
            except Exception as ffmpeg_err:
                logger.warning(
                    f"FFmpeg xatolik ({quality_label}): {ffmpeg_err} — xom fayl yuboriladi"
                )
                out_path = raw_path   # Fallback: watermarksiz original

            # ── 3. Hajm tekshiruvi ─────────────────────────
            size = os.path.getsize(out_path)

            if size <= MAX_FILE_SIZE:
                send_path = out_path
                send_size = size
                break   # Bu sifat mos keldi ✅

            # Hali ham katta → keyingi sifatni sinab ko'r
            logger.info(
                f"{quality_label} siqilgandan keyin ham katta: "
                f"{size / (1024*1024):.1f} MB > 50 MB"
            )
            if out_path != raw_path:
                await cleanup_file(out_path)
            out_path = None

        # ── Barcha sifatlar katta bo'lib qoldi ────────────
        if send_path is None:
            await status.edit_text(
                "❌ <b>Video hajmi juda katta!</b>\n\n"
                "Barcha sifat darajalarida (720p / 480p / 360p) ham\n"
                "50 MB dan oshib ketdi.\n\n"
                "Qisqaroq yoki kichikroq video yuboring.",
                reply_markup=main_menu_keyboard(),
            )
            return

        # ── 4. Telegramga yuborish ─────────────────────────
        await status.edit_text(f"📤 <b>Yuborilmoqda...</b>  ({format_size(send_size)})")

        wm_badge = "  ·  🎨 Watermark" if WATERMARK_ENABLED else ""
        await ref.answer_video(
            video=FSInputFile(send_path),
            caption=(
                f"✅ <b>Muvaffaqiyatli yuklandi!</b>\n\n"
                f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n"
                f"📦 Hajm: <b>{format_size(send_size)}</b>{wm_badge}\n\n"
                f"🤖 @laminox"
            ),
            reply_markup=main_menu_keyboard(),
        )
        await status.delete()

        # ── 5. DB logi + Admin xabarnomasi ────────────────
        await increment_downloads(user.id)
        await log_download(user.id, platform, "video", url)

        try:
            await notify_download(ref.bot, user, platform, "video", send_size, url)
        except Exception as notify_err:
            logger.debug(f"Notification xatolik: {notify_err}")

    except PermanentDownloadError as exc:
        logger.warning(f"Permanent xato [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Video mavjud emas yoki himoyalangan!</b>\n\n"
            f"{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error(f"Video xatolik [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
        # Barcha vaqtinchalik fayllarni tozalash
        if raw_path:
            await cleanup_file(raw_path)
        if out_path and out_path != raw_path:
            await cleanup_file(out_path)


# ══════════════════════════════════════════════════════════
# YADRO: audio yuklab olish (MP3 128 kbps)
# ══════════════════════════════════════════════════════════

async def _do_audio(ref: Message, user, url: str, platform: str) -> None:
    """Audio ajratib, MP3 128 kbps formatda yuboradi. Admin xabarnomasi."""
    emoji  = _PLATFORM_EMOJI.get(platform, "🌐")
    status = await ref.answer(
        f"⏳ <b>Audio ajratilmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  🎵 MP3 128kbps\n\n"
        f"🔄 Iltimos kuting..."
    )

    file_path: str | None = None
    try:
        file_path = await download_audio(url)
        size      = os.path.getsize(file_path)

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

        # DB logi + Admin xabarnomasi
        await increment_downloads(user.id)
        await log_download(user.id, platform, "audio", url)

        try:
            await notify_download(ref.bot, user, platform, "audio", size, url)
        except Exception as notify_err:
            logger.debug(f"Notification xatolik: {notify_err}")

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
# ══════════════════════════════════════════════════════════

@router.message(StateFilter(None), F.text.regexp(r"https?://\S+"))
async def handle_url_direct(message: Message, state: FSMContext, bot) -> None:
    url = message.text.strip()
    uid = message.from_user.id

    if not await check_subscription(bot, uid):
        await message.answer(
            f"🔒 Botdan foydalanish uchun kanalga obuna bo'ling:\n{CHANNEL_URL}",
            reply_markup=subscription_keyboard(),
        )
        return

    rem = _cooldown_remaining(uid)
    if rem:
        await message.answer(
            f"⏳ Iltimos <b>{rem}</b> soniya kuting!",
            reply_markup=main_menu_keyboard(),
        )
        return

    platform = detect_platform(url)
    if platform == "unknown":
        await message.answer(
            "❌ <b>Qo'llab-quvvatlanmaydigan platforma!</b>\n\n"
            "Qo'llab-quvvatlanadigan:\n"
            "├ 🔴  youtube.com / youtu.be\n"
            "├ 📸  instagram.com\n"
            "└ 🎵  tiktok.com",
            reply_markup=main_menu_keyboard(),
        )
        return

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


# ── Format tanlash: 🎥 Video ──────────────────────────────

@router.callback_query(DLState.waiting_format, F.data == "fmt_video")
async def cb_fmt_video(callback: CallbackQuery, state: FSMContext) -> None:
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
    await _do_video(callback.message, callback.from_user, url, platform)


# ── Format tanlash: 🎵 MP3 ───────────────────────────────

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
    await _do_audio(callback.message, callback.from_user, url, platform)


# waiting_format holatida boshqa matn kelsa
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


# ── FSM: Video URL → yuklab olish ────────────────────────

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
    await _do_video(message, message.from_user, url, platform)


# ── FSM: Audio URL → yuklab olish ────────────────────────

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
    await _do_audio(message, message.from_user, url, platform)
