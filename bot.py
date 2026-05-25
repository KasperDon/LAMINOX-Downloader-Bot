"""
bot.py
──────
LAMINOX Downloader Bot — asosiy kirish nuqtasi.

Ishga tushirish:
  python bot.py

TelegramConflictError — bitta polling instance ishlasin deb himoyalangan.
Graceful shutdown — SIGINT/SIGTERM bo'lganda tozalab to'xtaydi.
"""

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, COOKIES_PATH, YOUTUBE_COOKIES_ENABLED
from handlers import admin, download, start
from utils.database import init_db

# ── Papkalar ──────────────────────────────────────────────
os.makedirs("logs",      exist_ok=True)
os.makedirs("downloads", exist_ok=True)

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Startup tekshiruvlari
# ──────────────────────────────────────────────────────────

def _startup_checks() -> None:
    """Ishga tushishdan oldin muhit tekshiruvi."""
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN topilmadi! .env faylini tekshiring.")
        sys.exit(1)

    # Cookies holati
    if YOUTUBE_COOKIES_ENABLED:
        if os.path.exists(COOKIES_PATH):
            size_kb = os.path.getsize(COOKIES_PATH) / 1024
            logger.info(
                f"✅ YouTube cookies topildi: {os.path.abspath(COOKIES_PATH)} "
                f"({size_kb:.1f} KB)"
            )
        else:
            logger.warning(
                f"⚠️  YOUTUBE_COOKIES_ENABLED=true lekin "
                f"'{COOKIES_PATH}' topilmadi — "
                "YouTube so'rovlari xatolikka uchrashi mumkin."
            )
    else:
        logger.info("ℹ️  YouTube cookies o'chirilgan (YOUTUBE_COOKIES_ENABLED=false)")


# ──────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────

async def main() -> None:
    _startup_checks()

    # Ma'lumotlar bazasini ishga tushirish
    await init_db()
    logger.info("✅ Database tayyor")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    # ── Router tartibi MUHIM ──────────────────────────────
    # 1. admin   — /admin, /stats, /broadcast (komandalar)
    # 2. download — URL handler + FSM states (catch-all'dan OLDIN)
    # 3. start   — /start + @router.message() catch-all (HAR DOIM OXIRGI)
    dp.include_router(admin.router)
    dp.include_router(download.router)
    dp.include_router(start.router)

    bot_info = await bot.get_me()
    logger.info(f"🤖 Bot ishga tushdi: @{bot_info.username} (ID: {bot_info.id})")

    try:
        await dp.start_polling(
            bot,
            skip_updates=True,                          # Eski xabarlarni o'tkazib yubor
            allowed_updates=dp.resolve_used_update_types(),  # Faqat kerakli update turlari
        )
    except TelegramConflictError:
        logger.critical(
            "❌ TelegramConflictError: boshqa polling instance allaqachon ishlamoqda!\n"
            "   Railway'da bitta deploy bo'lishi kerak, lokal ishga tushirish o'chirilishi kerak."
        )
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot Ctrl+C bilan to'xtatildi.")
    except Exception as exc:
        logger.critical(f"Kutilmagan xato: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        await bot.session.close()
        logger.info("🔴 Bot to'xtatildi, session yopildi.")


if __name__ == "__main__":
    asyncio.run(main())
