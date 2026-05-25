from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import CHANNEL_URL


# ── Obuna ─────────────────────────────────────────────────

def subscription_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📢 Kanalga obuna bo'lish", url=CHANNEL_URL))
    kb.row(InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription"))
    return kb.as_markup()


# ── Asosiy menyu ──────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎥 Video yuklash", callback_data="download_video"),
        InlineKeyboardButton(text="🎵 MP3 yuklash",   callback_data="download_audio"),
    )
    kb.row(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="main_menu"))
    return kb.as_markup()


# ── Format tanlash (to'g'ridan-to'g'ri link yuborilganda) ─

def format_choice_keyboard() -> InlineKeyboardMarkup:
    """URL to'g'ridan-to'g'ri yuborilganda format so'rash."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎥 Video (MP4)", callback_data="fmt_video"),
        InlineKeyboardButton(text="🎵 Audio (MP3)", callback_data="fmt_audio"),
    )
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    return kb.as_markup()


# ── Umumiy yordamchi ──────────────────────────────────────

def cancel_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    return kb.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ Asosiy menyu", callback_data="main_menu"))
    return kb.as_markup()


def back_keyboard(target: str = "main_menu") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ Orqaga", callback_data=target))
    return kb.as_markup()


# ── Admin panel ───────────────────────────────────────────

def admin_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📊 Statistika",         callback_data="admin_stats"),
        InlineKeyboardButton(text="📋 So'nggi yuklamalar", callback_data="admin_last10"),
    )
    kb.row(
        InlineKeyboardButton(text="🏆 Top foydalanuvchilar", callback_data="admin_top10"),
    )
    kb.row(
        InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast"),
    )
    kb.row(
        InlineKeyboardButton(text="◀️ Chiqish", callback_data="main_menu"),
    )
    return kb.as_markup()


def admin_back_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ Admin panel", callback_data="admin_panel"))
    return kb.as_markup()
