import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import db
from bot.services.xui import XUIClient

logger = logging.getLogger(__name__)

ONE_GB = 1024 * 1024 * 1024
CHECK_INTERVAL = 1800  # 30 minutes


def _renew_keyboard(config_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 تمدید", callback_data=f"renew:{config_id}")]
    ])


async def _check_once(bot: Bot):
    configs = await db.get_all_active_configs()
    if not configs:
        return

    master = await db.get_master_server()
    xui = XUIClient(master["url"], api_token=master["api_token"]) if master else None
    now = datetime.now()

    for c in configs:
        try:
            if not c["reminder_time_sent"] and c["expire_date"]:
                seconds_left = (c["expire_date"] - now).total_seconds()
                if 0 < seconds_left <= 24 * 3600:
                    await bot.send_message(
                        chat_id=c["telegram_id"],
                        text=(
                            "⏳ زمان اشتراک شما رو به اتمام است!\n"
                            "کمتر از ۲۴ ساعت تا پایان باقی مانده.\n"
                            "در صورت تمایل می‌توانید تمدید کنید:"
                        ),
                        reply_markup=_renew_keyboard(c["id"]),
                    )
                    await db.mark_config_reminder(c["id"], "time")

            if not c["reminder_traffic_sent"] and xui:
                traffic = await xui.get_client_traffic(c["client_email"])
                if traffic.get("total", 0) > 0 and 0 <= traffic.get("remaining", 0) <= ONE_GB:
                    await bot.send_message(
                        chat_id=c["telegram_id"],
                        text=(
                            "📊 ترافیک اشتراک شما رو به اتمام است!\n"
                            "کمتر از ۱ گیگابایت باقی مانده.\n"
                            "در صورت تمایل می‌توانید تمدید کنید:"
                        ),
                        reply_markup=_renew_keyboard(c["id"]),
                    )
                    await db.mark_config_reminder(c["id"], "traffic")
        except Exception as e:
            logger.warning(f"reminder check failed for config {c['id']}: {type(e).__name__}: {e}")


async def _reminder_loop(bot: Bot):
    while True:
        try:
            await _check_once(bot)
        except Exception as e:
            logger.error(f"reminder loop error: {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


def start_reminders(bot: Bot):
    logger.info("Reminder scheduler started")
    return asyncio.create_task(_reminder_loop(bot))
