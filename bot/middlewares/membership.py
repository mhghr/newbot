import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from bot.config import CHANNEL_ID, ADMIN_IDS

logger = logging.getLogger(__name__)

MEMBER_STATUSES = ("member", "administrator", "creator")


async def check_membership(bot: Bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    if not CHANNEL_ID or CHANNEL_ID == 0:
        return True

    last_error = None
    for attempt in range(3):
        try:
            member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            status = getattr(member, "status", None)
            logger.info(f"User {user_id} membership status: {status}")
            if status in MEMBER_STATUSES:
                return True
            if status == "restricted" and getattr(member, "is_member", False):
                return True
            return False
        except TelegramNetworkError as e:
            last_error = e
            logger.warning(f"membership check network error (attempt {attempt + 1}/3): {e}")
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(
                f"Membership check failed for {user_id}: {e} "
                "(is the bot an admin of the channel? is CHANNEL_ID correct?)"
            )
            return False

    logger.error(f"Membership check gave up for {user_id} after network errors: {last_error}")
    return False
