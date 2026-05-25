from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from keyboards.inline import (
    back_to_menu_keyboard,
    main_menu_keyboard,
    subscription_keyboard,
)
from utils.checker import check_subscription
from utils.database import upsert_user
from config import CHANNEL_URL

router = Router()

_WELCOME = """
╔═══════════════════════════╗
    🎬  <b>MediaLoader Pro</b>
╚═══════════════════════════╝

Salom, <b>{name}</b>! 👋

🚀 <b>Nima qila olaman?</b>
┃
├ 🔴  YouTube video / Shorts
├ 📸  Instagram Reels / Post
├ 🎵  TikTok video
├ 🎧  MP3 audio (har qanday platformadan)
└ ⚡  Tez va yuqori sifat

━━━━━━━━━━━━━━━━━━━━━━━━━━━
👇 Pastdagi tugmani tanlang:
"""

_SUBSCRIPTION_REQUIRED = """
🔒 <b>Kanalga obuna bo'lish shart!</b>

Botdan foydalanish uchun avval bizning kanalga
obuna bo'lishingiz kerak.

📢 Kanal: {url}

Obuna bo'lgach <b>✅ Tekshirish</b> tugmasini bosing.
"""

_HELP = """
╔═══════════════════════════╗
       ℹ️ <b>Yordam</b>
╚═══════════════════════════╝

<b>📥 Qanday foydalanish:</b>

<b>1. 🎥 Video yuklash:</b>
   → "🎥 Video yuklash" tugmasini bosing
   → Video linkini yuboring
   → Bot MP4 formatda yuboradi

<b>2. 🎵 MP3 yuklash:</b>
   → "🎵 MP3 yuklash" tugmasini bosing
   → Link yuboring
   → Bot MP3 sifatida yuboradi

━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🌐 Qo'llab-quvvatlanadigan saytlar:</b>
├ 🔴  YouTube (video, Shorts)
├ 📸  Instagram (Reels, Post)
└ 🎵  TikTok

━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ <b>Eslatma:</b>
• Faqat ommaviy (public) kontent
• Fayl hajmi ≤ 50 MB
• Har so'rovdan keyin biroz kuting

📩 Muammo: @laminox
"""


@router.message(CommandStart())
async def cmd_start(message: Message, bot) -> None:
    user = message.from_user
    await upsert_user(user.id, user.username or "", user.full_name)

    if not await check_subscription(bot, user.id):
        await message.answer(
            _SUBSCRIPTION_REQUIRED.format(url=CHANNEL_URL),
            reply_markup=subscription_keyboard(),
        )
        return

    await message.answer(
        _WELCOME.format(name=user.first_name),
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery, bot) -> None:
    if not await check_subscription(bot, callback.from_user.id):
        await callback.answer(
            "❌ Hali obuna bo'lmagansiz! Avval kanalga obuna bo'ling.",
            show_alert=True,
        )
        return

    await callback.message.delete()
    await callback.message.answer(
        _WELCOME.format(name=callback.from_user.first_name),
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer("✅ Obuna tasdiqlandi!")


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        _WELCOME.format(name=callback.from_user.first_name),
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.message.edit_text(_HELP, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer(
        "❓ Noma'lum buyruq.\n\n/start — asosiy menyuni ochish",
        reply_markup=main_menu_keyboard(),
    )
