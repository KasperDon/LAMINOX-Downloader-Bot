"""
handlers/admin.py
─────────────────
Admin komandalar va callback'lar:
  /admin   — admin panel (qisqa statistika + tugmalar)
  /stats   — to'liq statistika (bugungi + jami + top + oxirgi)
  /broadcast — barcha foydalanuvchilarga xabar yuborish

Xavfsizlik: ADMIN_IDS ro'yxatidan boshqa hech kim kira olmaydi.
"""

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from keyboards.inline import (
    admin_back_keyboard,
    admin_keyboard,
    back_keyboard,
    main_menu_keyboard,
)
from utils.database import get_all_user_ids, get_full_stats, get_stats

logger = logging.getLogger(__name__)
router = Router()

_PLATFORM_EMOJI = {"youtube": "🔴", "instagram": "📸", "tiktok": "🎵"}
_TYPE_EMOJI     = {"video": "🎥", "audio": "🎵"}


# ──────────────────────────────────────────────────────────
# Yordamchi: ruxsat tekshiruvi
# ──────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _deny(source: Message | CallbackQuery) -> None:
    """Ruxsat yo'q xabari."""
    text = "🚫 <b>Ruxsat yo'q!</b>\n\nSiz admin emassiz."
    if isinstance(source, CallbackQuery):
        await source.answer(text, show_alert=True)
    else:
        await source.answer(text)


# ──────────────────────────────────────────────────────────
# Matn generatorlar
# ──────────────────────────────────────────────────────────

def _panel_text(s: dict) -> str:
    return (
        "╔═══════════════════════════╗\n"
        "      🛠  <b>Admin Panel</b>\n"
        "╚═══════════════════════════╝\n\n"
        "📊 <b>Qisqa statistika:</b>\n"
        f"├ 👥  Foydalanuvchilar:  <b>{s['users']}</b>\n"
        f"├ 📥  Jami yuklamalar:   <b>{s['downloads']}</b>\n"
        f"├ 🎥  Video:             <b>{s['video']}</b>\n"
        f"├ 🎵  Audio (MP3):       <b>{s['audio']}</b>\n"
        f"├ 🔴  YouTube:           <b>{s['youtube']}</b>\n"
        f"├ 📸  Instagram:         <b>{s['instagram']}</b>\n"
        f"└ 🎵  TikTok:            <b>{s['tiktok']}</b>\n\n"
        "👇 Batafsil ma'lumot uchun tugmani bosing:"
    )


def _full_stats_text(s: dict) -> str:
    today_block = (
        "📅 <b>Bugun:</b>\n"
        f"├ 🆕  Yangi foydalanuvchilar: <b>{s['today_users']}</b>\n"
        f"├ 👤  Faol foydalanuvchilar:  <b>{s['today_active']}</b>\n"
        f"├ 📥  Jami yuklamalar:        <b>{s['today_downloads']}</b>\n"
        f"├ 🎥  Video:                  <b>{s['today_video']}</b>\n"
        f"└ 🎵  Audio (MP3):            <b>{s['today_audio']}</b>"
    )
    total_block = (
        "📊 <b>Jami barcha vaqt:</b>\n"
        f"├ 👥  Foydalanuvchilar: <b>{s['users']}</b>\n"
        f"├ 📥  Yuklamalar:       <b>{s['downloads']}</b>\n"
        f"├ 🎥  Video:            <b>{s['video']}</b>\n"
        f"├ 🎵  Audio (MP3):      <b>{s['audio']}</b>\n"
        f"├ 🔴  YouTube:          <b>{s['youtube']}</b>\n"
        f"├ 📸  Instagram:        <b>{s['instagram']}</b>\n"
        f"└ 🎵  TikTok:           <b>{s['tiktok']}</b>"
    )
    return (
        "╔═══════════════════════════╗\n"
        "    📊  <b>To'liq Statistika</b>\n"
        "╚═══════════════════════════╝\n\n"
        f"{today_block}\n\n"
        f"{total_block}"
    )


def _last10_text(rows: list[dict]) -> str:
    if not rows:
        return "📋 <b>So'nggi yuklamalar</b>\n\nHali hech narsa yuklanmagan."
    lines = ["📋 <b>So'nggi 10 ta yuklab olish:</b>\n"]
    for i, r in enumerate(rows, 1):
        pe = _PLATFORM_EMOJI.get(r["platform"], "🌐")
        te = _TYPE_EMOJI.get(r["media_type"], "📁")
        t  = r["dl_time"].replace("T", " ")
        lines.append(
            f"{i}. {pe}{te} <b>{r['display_name']}</b> — {t}"
        )
    return "\n".join(lines)


def _top10_text(rows: list[dict]) -> str:
    if not rows:
        return "🏆 <b>Top foydalanuvchilar</b>\n\nHali hech kim yuklamagan."
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Eng faol 10 ta foydalanuvchi:</b>\n"]
    for i, r in enumerate(rows, 1):
        m = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(
            f"{m} <b>{r['display_name']}</b> — {r['dl_count']} ta yuklab olish"
        )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# /admin
# ──────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await _deny(message)
        return
    s = await get_stats()
    await message.answer(_panel_text(s), reply_markup=admin_keyboard())


# ──────────────────────────────────────────────────────────
# /stats — to'liq statistika
# ──────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await _deny(message)
        return
    wait = await message.answer("⏳ Statistika yuklanmoqda...")
    s = await get_full_stats()
    await wait.delete()
    await message.answer(_full_stats_text(s), reply_markup=admin_back_keyboard())


# ──────────────────────────────────────────────────────────
# /broadcast
# ──────────────────────────────────────────────────────────

class BroadcastState(StatesGroup):
    waiting_message = State()


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await _deny(message)
        return
    await state.set_state(BroadcastState.waiting_message)
    await message.answer(
        "📢 <b>Broadcast</b>\n\n"
        "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring.\n\n"
        "Bekor qilish uchun: /cancel",
        reply_markup=back_keyboard("admin_panel"),
    )


@router.message(BroadcastState.waiting_message)
async def process_broadcast(message: Message, state: FSMContext, bot) -> None:
    if not _is_admin(message.from_user.id):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Broadcast bekor qilindi.", reply_markup=main_menu_keyboard())
        return

    await state.clear()
    user_ids = await get_all_user_ids()
    total    = len(user_ids)

    progress = await message.answer(f"📤 Yuborilmoqda...  0 / {total}")
    success, failed = 0, 0

    for idx, uid in enumerate(user_ids, start=1):
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            success += 1
        except Exception:
            failed += 1

        if idx % 25 == 0:
            try:
                await progress.edit_text(f"📤 Yuborilmoqda...  {idx} / {total}")
            except Exception:
                pass

        await asyncio.sleep(0.05)  # Telegram flood limit

    await progress.edit_text(
        "✅ <b>Broadcast yakunlandi!</b>\n\n"
        f"✉️ Yuborildi: <b>{success}</b>\n"
        f"❌ Xatolik:   <b>{failed}</b>\n"
        f"👥 Jami:      <b>{total}</b>"
    )
    logger.info(f"Broadcast: {success}/{total} sent, {failed} failed")


# ──────────────────────────────────────────────────────────
# Callback: admin_panel (back tugmasi)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await _deny(callback)
        return
    s = await get_stats()
    await callback.message.edit_text(_panel_text(s), reply_markup=admin_keyboard())
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: 📊 Statistika
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await _deny(callback)
        return
    await callback.answer("⏳ Yuklanmoqda...")
    s = await get_full_stats()
    await callback.message.edit_text(_full_stats_text(s), reply_markup=admin_back_keyboard())


# ──────────────────────────────────────────────────────────
# Callback: 📋 So'nggi 10 ta yuklab olish
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_last10")
async def cb_admin_last10(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await _deny(callback)
        return
    await callback.answer("⏳ Yuklanmoqda...")
    s = await get_full_stats()
    await callback.message.edit_text(
        _last10_text(s["last_10"]),
        reply_markup=admin_back_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# Callback: 🏆 Top 10 foydalanuvchilar
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_top10")
async def cb_admin_top10(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await _deny(callback)
        return
    await callback.answer("⏳ Yuklanmoqda...")
    s = await get_full_stats()
    await callback.message.edit_text(
        _top10_text(s["top_10"]),
        reply_markup=admin_back_keyboard(),
    )


# ──────────────────────────────────────────────────────────
# Callback: 📢 Broadcast (tugmadan)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await _deny(callback)
        return
    await state.set_state(BroadcastState.waiting_message)
    await callback.message.edit_text(
        "📢 <b>Broadcast</b>\n\n"
        "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring.\n\n"
        "Bekor qilish uchun: /cancel",
        reply_markup=back_keyboard("admin_panel"),
    )
    await callback.answer()
