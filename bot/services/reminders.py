import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import db
from bot.services.xui import XUIClient
from bot.services import config_ops

logger = logging.getLogger(__name__)

ONE_GB = 1024 * 1024 * 1024
CHECK_INTERVAL = 1800  # 30 minutes

# An account whose subscription expired more than this many days ago and was
# never renewed is removed from the panel (and soft-deleted in the DB).
AUTO_DELETE_AFTER_DAYS = 3
CLEANUP_INTERVAL = 24 * 3600  # run the account cleanup once a day


def _renew_keyboard(config_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 تمدید", callback_data=f"renew:{config_id}")]
    ])


async def _send_time_reminder(bot: Bot, config):
    await bot.send_message(
        chat_id=config["telegram_id"],
        text=(
            "⏳ زمان اشتراک شما رو به اتمام است!\n"
            "کمتر از ۲۴ ساعت تا پایان باقی مانده.\n"
            "در صورت تمایل می‌توانید تمدید کنید:"
        ),
        reply_markup=_renew_keyboard(config["id"]),
    )
    await db.mark_config_reminder(config["id"], "time")


async def _send_traffic_reminder(bot: Bot, config):
    await bot.send_message(
        chat_id=config["telegram_id"],
        text=(
            "📊 ترافیک اشتراک شما رو به اتمام است!\n"
            "کمتر از ۱ گیگابایت باقی مانده.\n"
            "در صورت تمایل می‌توانید تمدید کنید:"
        ),
        reply_markup=_renew_keyboard(config["id"]),
    )
    await db.mark_config_reminder(config["id"], "traffic")


async def _check_v2ray_config(bot: Bot, config, now: datetime, xui):
    if not config["reminder_time_sent"] and config["expire_date"]:
        seconds_left = (config["expire_date"] - now).total_seconds()
        if 0 < seconds_left <= 24 * 3600:
            await _send_time_reminder(bot, config)

    if not config["reminder_traffic_sent"]:
        # Sum traffic over all device clients of this account.
        from bot.services import account_view
        traffic = await account_view.traffic_summary(config)
        if (traffic.get("total") or 0) > 0 and 0 <= (traffic.get("remaining") or 0) <= ONE_GB:
            await _send_traffic_reminder(bot, config)


async def _cleanup_expired(bot: Bot, configs, now: datetime):
    """Remove accounts expired more than AUTO_DELETE_AFTER_DAYS ago without renewal.

    Deletes the client from the 3x-ui panel and soft-deletes the config row.
    The Telegram user is never touched.
    """
    cutoff = now - timedelta(days=AUTO_DELETE_AFTER_DAYS)
    for c in configs:
        expire = c.get("expire_date")
        if not expire or expire > cutoff:
            continue
        try:
            remote_ok = await config_ops.delete_config(c)
            logger.info(
                "Auto-removed expired account: config %s (%s, expired %s), remote_ok=%s",
                c["id"], c.get("client_email"), expire, remote_ok,
            )
        except Exception as e:
            logger.warning(
                "Auto-remove failed for config %s: %s: %s", c["id"], type(e).__name__, e
            )


async def _check_once(bot: Bot):
    configs = await db.get_all_active_configs()
    if not configs:
        return

    master = await db.get_master_server("v2ray")
    xui = XUIClient(master["url"], api_token=master["api_token"]) if master else None
    now = datetime.now()

    for c in configs:
        try:
            await _check_v2ray_config(bot, c, now, xui)
        except Exception as e:
            logger.warning(f"reminder check failed for config {c['id']}: {type(e).__name__}: {e}")


async def _reminder_loop(bot: Bot):
    while True:
        try:
            await _check_once(bot)
        except Exception as e:
            logger.error(f"reminder loop error: {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


async def _cleanup_loop(bot: Bot):
    while True:
        try:
            configs = await db.get_all_active_configs()
            await _cleanup_expired(bot, configs, datetime.now())
        except Exception as e:
            logger.error(f"cleanup loop error: {type(e).__name__}: {e}")
        await asyncio.sleep(CLEANUP_INTERVAL)


def start_reminders(bot: Bot):
    logger.info("Reminder scheduler started")
    asyncio.create_task(_cleanup_loop(bot))
    return asyncio.create_task(_reminder_loop(bot))
