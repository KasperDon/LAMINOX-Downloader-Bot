import logging
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from config import CHANNEL_ID

logger = logging.getLogger(__name__)

_BLOCKED_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status not in _BLOCKED_STATUSES
    except Exception as e:
        logger.warning(f"Obuna tekshirishda xatolik (user={user_id}): {e}")
        return False
