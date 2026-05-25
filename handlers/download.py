"""
handlers/download.py
────────────────────
Video va MP3 yuklab olish — 2 xil rejim:

  REJIM 1 — Tugma orqali (FSM):
    Tugma → waiting_video_url / waiting_audio_url → link → yuklab olish

  REJIM 2 — To'g'ridan-to'g'ri link:
    Link → platforma aniqlanadi → format so'raladi → yuklab olish

Video yuklash strategiyasi (50 MB limit):
  ┌─ 1. 720p yuklab olish → FFmpeg CRF-28 siqish (+ ixtiyoriy watermark)
  │     Agar ≤ 50 MB  → send_video ✅
  │     Agar > 50 MB  → 480p bilan qayta
  ├─ 2. 480p → FFmpeg → hajm tekshiruvi
  │     Agar > 50 MB  → 360p bilan qayta
  └─ 3. 360p → FFmpeg → send_video yoki send_document (fallback)

YouTube xatolari:
  YouTubeAuthError    → professional xabar (cookies kerak)
  PermanentDownloadError → video mavjud emas/himoyalangan

Admin bildirish:
  Har bir muvaffaqiyatli yuklab olishdan keyin adminlarga xabar yuboriladi.
"""

import logging
import os
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
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
    YouTubeAuthError,
    YouTubePlayerError,
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

# Katta video threshold: agar send_video ishlamasa, send_document sinaydi
_DOC_FALLBACK_SIZE = MAX_FILE_SIZE  # 50 MB


# ── FSM holatlari ─────────────────────────────────────────

class DLState(StatesGroup):
    waiting_video_url = State()
    waiting_audio_url = State()
    waiting_format    = State()


# ── Cooldown ──────────────────────────────────────────────

def _cooldown_remaining(user_id: int) -> int:
    ts = _cooldowns.get(user_id)
    return max(0, int(COOLDOWN_SECONDS - (time.monotonic() - ts))) if ts else 0


def _set_cooldown(user_id: int) -> None:
    _cooldowns[user_id] = time.monotonic()


# ══════════════════════════════════════════════════════════
# YADRO: video yuklab olish (cascading quality + doc fallback)
# ══════════════════════════════════════════════════════════

async def _do_video(ref: Message, user, url: str, platform: str) -> None:
    """
    Video yuklab olib, FFmpeg bilan siqib (+ watermark), Telegramga yuboradi.

    Agar 50 MB dan katta bo'lsa, avtomatik pastroq sifatda qayta urinadi.
    Agar send_video ishlamasa, send_document (fayl sifatida) yuboradi.
    """
    emoji    = _PLATFORM_EMOJI.get(platform, "🌐")
    wm_label = "  ·  🎨 watermark" if WATERMARK_ENABLED else ""

    status = await ref.answer(
        f"⏳ <b>Yuklab olinmoqda...</b>\n\n"
        f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4{wm_label}\n\n"
        f"🔄 Iltimos kuting...\n"
        f"<i>YouTube uchun ~30-60 soniya ketishi mumkin</i>"
    )

    raw_path: str | None  = None
    out_path: str | None  = None
    send_path: str | None = None
    send_size: int        = 0

    try:
        for i, (quality_label, fmt) in enumerate(QUALITY_PRESETS):

            # ── Keyingi sifat uchun xabar ─────────────────
            if i > 0:
                await status.edit_text(
                    f"📐 <b>Video hajmi katta — {quality_label}da qayta tayyorlanmoqda...</b>\n\n"
                    f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4\n\n"
                    f"🔄 Iltimos kuting..."
                )
                if raw_path:
                    await cleanup_file(raw_path)
                    raw_path = None

            # ── 1. yt-dlp: xom video ──────────────────────
            try:
                raw_path = await download_raw_video(url, fmt)
            except (YouTubeAuthError, PermanentDownloadError, YouTubePlayerError):
                raise  # Sifat o'zgarishi bu xatolarni hal qilmaydi
            except Exception:
                if i < len(QUALITY_PRESETS) - 1:
                    continue
                raise

            # ── 2. FFmpeg: siqish + watermark (bir o'tish) ─
            await status.edit_text(
                f"⚙️ <b>{quality_label}: tayyorlanmoqda"
                f"{'  · 🎨 watermark' if WATERMARK_ENABLED else ''}...</b>\n\n"
                f"{emoji} <b>{platform.capitalize()}</b>  →  📹 MP4\n\n"
                f"🔄 Iltimos kuting..."
            )

            if out_path and out_path != raw_path:
                await cleanup_file(out_path)
            out_path = None

            try:
                out_path = await process_video(raw_path, watermark=WATERMARK_ENABLED)
            except Exception as ffmpeg_err:
                logger.warning(
                    f"FFmpeg xatolik ({quality_label}): {ffmpeg_err} — xom fayl ishlatiladi"
                )
                out_path = raw_path  # FFmpeg ishlamasa, xom faylni yuborish

            # ── 3. Hajm tekshiruvi ─────────────────────────
            size = os.path.getsize(out_path)
            logger.info(
                f"Hajm tekshiruvi [{quality_label}]: "
                f"{size / (1024*1024):.1f} MB / 50 MB limit"
            )

            if size <= MAX_FILE_SIZE:
                send_path = out_path
                send_size = size
                break

            # Hali katta → keyingi sifat
            if out_path != raw_path:
                await cleanup_file(out_path)
            out_path = None

        # ── Barcha sifatlar exhausted ─────────────────────
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
        await status.edit_text(
            f"📤 <b>Yuborilmoqda...</b>  ({format_size(send_size)})"
        )

        wm_badge = "  ·  🎨 Watermark" if WATERMARK_ENABLED else ""
        caption  = (
            f"✅ <b>Muvaffaqiyatli yuklandi!</b>\n\n"
            f"{emoji} Platforma: <b>{platform.capitalize()}</b>\n"
            f"📦 Hajm: <b>{format_size(send_size)}</b>{wm_badge}\n\n"
            f"🤖 @laminox"
        )

        sent = False

        # send_video — Telegram ichida to'g'ridan-to'g'ri o'ynaydi
        try:
            await ref.answer_video(
                video=FSInputFile(send_path),
                caption=caption,
                reply_markup=main_menu_keyboard(),
            )
            sent = True
        except TelegramAPIError as api_err:
            logger.warning(
                f"send_video muvaffaqiyatsiz: {api_err} — send_document sinab ko'riladi"
            )

        # send_document fallback — video player ishlamasa fayl sifatida yuboradi
        if not sent:
            try:
                await ref.answer_document(
                    document=FSInputFile(send_path),
                    caption=caption + "\n\n<i>📎 Fayl sifatida yuborildi</i>",
                    reply_markup=main_menu_keyboard(),
                )
                sent = True
            except TelegramAPIError as doc_err:
                logger.error(f"send_document ham muvaffaqiyatsiz: {doc_err}")
                raise Exception(str(doc_err))

        if sent:
            await status.delete()

            # ── 5. DB + Admin notification ─────────────────
            await increment_downloads(user.id)
            await log_download(user.id, platform, "video", url)
            try:
                await notify_download(ref.bot, user, platform, "video", send_size, url)
            except Exception as notify_err:
                logger.debug(f"Notification xatolik: {notify_err}")

    except YouTubePlayerError:
        logger.warning(f"YouTube player xatosi [{platform}]: barcha client'lar muvaffaqiyatsiz")
        await status.edit_text(
            "⚠️ <b>YouTube video yuklanmadi!</b>\n\n"
            "YouTube hozir bu videoga kirishni cheklamoqda.\n\n"
            "📌 Nima qilish mumkin:\n"
            "• Linkni tekshiring — video <b>ochiq (public)</b> bo'lishi kerak\n"
            "• Boshqa YouTube linkni sinab ko'ring\n"
            "• <b>Shorts</b> link bo'lsa — to'liq URL yuboring\n"
            "• 1-2 daqiqadan so'ng qayta urinib ko'ring\n"
            "• <b>Instagram</b> yoki <b>TikTok</b> link ham yuborishingiz mumkin",
            reply_markup=main_menu_keyboard(),
        )
    except YouTubeAuthError:
        logger.warning(f"YouTube auth xatolik [{platform}]: bot taniqlash")
        await status.edit_text(
            "⚠️ <b>YouTube vaqtincha ishlamayapti!</b>\n\n"
            "YouTube bot taniqlash tizimini ishga tushirdi.\n\n"
            "Admin <code>cookies.txt</code> faylini serverga "
            "joylashtirishi yoki yangilashi kerak.\n\n"
            "🔄 Bir oz vaqt o'tgach qayta urinib ko'ring.",
            reply_markup=main_menu_keyboard(),
        )
    except PermanentDownloadError as exc:
        logger.warning(f"Permanent xato [{platform}]: {exc}")
        await status.edit_text(
            f"❌ <b>Video mavjud emas yoki himoyalangan!</b>\n\n"
            f"{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error(f"Video xatolik [{platform}]: {exc}", exc_info=True)
        await status.edit_text(
            f"❌ <b>Xatolik yuz berdi!</b>\n\n{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    finally:
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
                "Audio 50 MB dan oshib ketdi.",
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

        await increment_downloads(user.id)
        await log_download(user.id, platform, "audio", url)
        try:
            await notify_download(ref.bot, user, platform, "audio", size, url)
        except Exception as notify_err:
            logger.debug(f"Notification xatolik: {notify_err}")

    except YouTubePlayerError:
        await status.edit_text(
            "⚠️ <b>YouTube audio yuklanmadi!</b>\n\n"
            "YouTube hozir bu videoga kirishni cheklamoqda.\n\n"
            "📌 Nima qilish mumkin:\n"
            "• Linkni tekshiring — video <b>ochiq (public)</b> bo'lishi kerak\n"
            "• Boshqa YouTube linkni sinab ko'ring\n"
            "• 1-2 daqiqadan so'ng qayta urinib ko'ring\n"
            "• <b>Instagram</b> yoki <b>TikTok</b> link ham yuborishingiz mumkin",
            reply_markup=main_menu_keyboard(),
        )
    except YouTubeAuthError:
        await status.edit_text(
            "⚠️ <b>YouTube vaqtincha ishlamayapti!</b>\n\n"
            "YouTube bot taniqlash tizimini ishga tushirdi.\n\n"
            "Admin <code>cookies.txt</code> faylini serverga "
            "joylashtirishi yoki yangilashi kerak.\n\n"
            "🔄 Keyinroq qayta urinib ko'ring.",
            reply_markup=main_menu_keyboard(),
        )
    except PermanentDownloadError as exc:
        await status.edit_text(
            f"❌ <b>Kontent mavjud emas yoki himoyalangan!</b>\n\n"
            f"{user_friendly_error(str(exc))}",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error(f"Audio xatolik [{platform}]: {exc}", exc_info=True)
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
