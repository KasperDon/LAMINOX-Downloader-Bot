"""
handlers/start.py
─────────────────
/start komandasi, obuna tekshiruvi, yordam.

Admin bildirishnomasi:
  Foydalanuvchi BIRINCHI MARTA /start bosganida adminlarga xabar yuboriladi.
  Qayta-qayta bosilganda spam bo'lmasligi uchun is_new_user() tekshiruvi bor.
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from config import CHANNEL_URL
from keyboards.inline import back_to_menu_keyboard, main_menu_keyboard, subscription_keyboard
from utils.checker import check_subscription
from utils.database import is_new_user, upsert_user
from utils.notifications import notify_new_user

router = Router()

# ── Matnlar ───────────────────────────────────────────────

_WELCOME = (
    "╔═══════════════════════════╗\n"
    "    🎬  <b>LAMINOX Downloader</b>\n"
    "╚═══════════════════════════╝\n\n"
    "Salom, <b>{name}</b>! 👋\n\n"
    "🚀 <b>Nima qila olaman?</b>\n"
    "┃\n"
    "├ 🔴  YouTube video / Shorts\n"
    "├ 📸  Instagram Reels / Post\n"
    "├ 🎵  TikTok video\n"
    "├ 🎧  MP3 audio (har qanday platformadan)\n"
    "└ ⚡  Tez va yuqori sifat\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👇 Tugmani bosing yoki to'g'ridan-to'g'ri link yuboring:"
)

_SUBSCRIPTION_REQUIRED = (
    "🔒 <b>Kanalga obuna bo'lish shart!</b>\n\n"
    "Botdan foydalanish uchun avval bizning kanalga\n"
    "obuna bo'lishingiz kerak.\n\n"
    "📢 Kanal: {url}\n\n"
    "Obuna bo'lgach <b>✅ Tekshirish</b> tugmasini bosing."
)

_HELP = (
    "╔═══════════════════════════╗\n"
    "       ℹ️ <b>Yordam</b>\n"
    "╚═══════════════════════════╝\n\n"
    "<b>📥 Qanday foydalanish:</b>\n\n"
    "<b>Usul 1 — Tugma orqali:</b>\n"
    "   → 🎥 yoki 🎵 tugmasini bosing\n"
    "   → Link yuboring\n\n"
    "<b>Usul 2 — To'g'ridan-to'g'ri:</b>\n"
    "   → Link yuboring\n"
    "   → Format tanlang (Video / MP3)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>🌐 Qo'llab-quvvatlanadigan saytlar:</b>\n"
    "├ 🔴  YouTube (video, Shorts)\n"
    "├ 📸  Instagram (Reels, Post)\n"
    "└ 🎵  TikTok\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "⚠️ <b>Eslatma:</b>\n"
    "• Faqat ommaviy (public) kontent\n"
    "• Fayl hajmi ≤ 50 MB\n"
    "• Har so'rovdan keyin biroz kuting\n\n"
    "📩 Muammo: @laminox"
)


# ── /start ────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, bot) -> None:
    user = message.from_user

    # Yangi foydalanuvchimi — UPSERT dan OLDIN tekshiriladi
    new = await is_new_user(user.id)

    # Foydalanuvchini bazaga qo'sh / yangilab qo'y
    await upsert_user(user.id, user.username or "", user.full_name)

    # Faqat birinchi marta bosganida adminlarga xabar
    if new:
        await notify_new_user(bot, user)

    # Obuna tekshiruvi
    if not await check_subscription(bot, user.id):
        await message.answer(
            _SUBSCRIPTION_REQUIRED.format(url=CHANNEL_URL),
            reply_markup=subscription_keyboard(),
        )
        return

    await message.answer(_WELCOME.format(name=user.first_name), reply_markup=main_menu_keyboard())


# ── Obuna tekshiruvi callback ─────────────────────────────

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


# ── Asosiy menyu callback ─────────────────────────────────

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        _WELCOME.format(name=callback.from_user.first_name),
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# ── Yordam callback ───────────────────────────────────────

@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.message.edit_text(_HELP, reply_markup=back_to_menu_keyboard())
    await callback.answer()


# ── Catch-all (HAR DOIM OXIRGI) ───────────────────────────

@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer(
        "❓ Noma'lum buyruq.\n\n"
        "Link yuboring yoki /start ni bosing.",
        reply_markup=main_menu_keyboard(),
    )
