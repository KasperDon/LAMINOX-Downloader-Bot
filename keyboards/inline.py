from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import CHANNEL_URL


def subscription_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📢 Kanalga obuna bo'lish", url=CHANNEL_URL))
    kb.row(InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription"))
    return kb.as_markup()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎥 Video yuklash", callback_data="download_video"),
        InlineKeyboardButton(text="🎵 MP3 yuklash", callback_data="download_audio"),
    )
    kb.row(InlineKeyboardButton(text="ℹ️ Yordam", callback_data="help"))
    return kb.as_markup()


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


def admin_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats"))
    kb.row(InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast"))
    kb.row(InlineKeyboardButton(text="◀️ Chiqish", callback_data="main_menu"))
    return kb.as_markup()
