import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from handlers import admin, download, start
from utils.database import init_db

os.makedirs("logs", exist_ok=True)
os.makedirs("downloads", exist_ok=True)

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


async def main() -> None:
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN topilmadi! .env faylini tekshiring.")
        sys.exit(1)

    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start.router)
    dp.include_router(download.router)
    dp.include_router(admin.router)

    bot_info = await bot.get_me()
    logger.info(f"Bot ishga tushdi: @{bot_info.username} ({bot_info.id})")

    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.session.close()
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    asyncio.run(main())
