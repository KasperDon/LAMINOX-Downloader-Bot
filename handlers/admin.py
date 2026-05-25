import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from keyboards.inline import admin_keyboard, back_keyboard, main_menu_keyboard
from utils.database import get_all_user_ids, get_stats

logger = logging.getLogger(__name__)
router = Router()


class BroadcastState(StatesGroup):
    waiting_message = State()


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _stats_text(stats: dict) -> str:
    return (
        "╔═══════════════════════════╗\n"
        "      🛠  <b>Admin Panel</b>\n"
        "╚═══════════════════════════╝\n\n"
        "📊 <b>Statistika:</b>\n"
        f"├ 👥  Foydalanuvchilar: <b>{stats['users']}</b>\n"
        f"├ 📥  Jami yuklamalar:  <b>{stats['downloads']}</b>\n"
        f"├ 🔴  YouTube:          <b>{stats['youtube']}</b>\n"
        f"├ 📸  Instagram:        <b>{stats['instagram']}</b>\n"
        f"├ 🎵  TikTok:           <b>{stats['tiktok']}</b>\n"
        f"├ 🎥  Video:            <b>{stats['video']}</b>\n"
        f"└ 🎵  Audio:            <b>{stats['audio']}</b>"
    )


# ──────────────────────────────────────────────────────────
# /admin
# ──────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("❌ Sizda admin huquqi yo'q!")
        return

    stats = await get_stats()
    await message.answer(_stats_text(stats), reply_markup=admin_keyboard())


# ──────────────────────────────────────────────────────────
# Callback: admin_panel (back)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
        return

    stats = await get_stats()
    await callback.message.edit_text(_stats_text(stats), reply_markup=admin_keyboard())
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: Statistics
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
        return

    stats = await get_stats()
    text = (
        "📊 <b>Batafsil Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{stats['users']}</b>\n"
        f"📥 Jami yuklamalar:  <b>{stats['downloads']}</b>\n\n"
        "<b>Platforma bo'yicha:</b>\n"
        f"├ 🔴  YouTube:   <b>{stats['youtube']}</b>\n"
        f"├ 📸  Instagram: <b>{stats['instagram']}</b>\n"
        f"└ 🎵  TikTok:    <b>{stats['tiktok']}</b>\n\n"
        "<b>Format bo'yicha:</b>\n"
        f"├ 🎥  Video: <b>{stats['video']}</b>\n"
        f"└ 🎵  Audio: <b>{stats['audio']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=back_keyboard("admin_panel"))
    await callback.answer()


# ──────────────────────────────────────────────────────────
# Callback: Broadcast start
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
        return

    await state.set_state(BroadcastState.waiting_message)
    await callback.message.edit_text(
        "📢 <b>Broadcast</b>\n\n"
        "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring.\n\n"
        "Bekor qilish uchun /cancel yozing.",
        reply_markup=back_keyboard("admin_panel"),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────
# State: Send broadcast
# ──────────────────────────────────────────────────────────

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
    total = len(user_ids)

    progress = await message.answer(f"📤 Yuborilmoqda...  0 / {total}")

    success = 0
    failed = 0

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

        if idx % 20 == 0:
            try:
                await progress.edit_text(f"📤 Yuborilmoqda...  {idx} / {total}")
            except Exception:
                pass

        await asyncio.sleep(0.05)

    await progress.edit_text(
        f"✅ <b>Broadcast yakunlandi!</b>\n\n"
        f"✉️ Yuborildi: <b>{success}</b>\n"
        f"❌ Xatolik:   <b>{failed}</b>\n"
        f"👥 Jami:      <b>{total}</b>"
    )
    logger.info(f"Broadcast: {success}/{total} sent, {failed} failed")
