import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import db
from bot.services.xui import XUIClient
from bot.services import wireguard as wg

logger = logging.getLogger(__name__)

ONE_GB = 1024 * 1024 * 1024
CHECK_INTERVAL = 1800  # 30 minutes


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

    if not config["reminder_traffic_sent"] and xui:
        traffic = await xui.get_client_traffic(config["client_email"])
        if traffic.get("total", 0) > 0 and 0 <= traffic.get("remaining", 0) <= ONE_GB:
            await _send_traffic_reminder(bot, config)


async def _get_wg_server(cache: dict, config):
    server_id = config["server_id"]
    if not server_id:
        return None
    if server_id not in cache:
        cache[server_id] = await db.get_server(server_id)
    return cache[server_id]


async def _check_wg_config(bot: Bot, config, now: datetime, server_cache: dict, usage_cache: dict):
    server = await _get_wg_server(server_cache, config)
    if not server or (server["service_type"] or "v2ray") != "wireguard":
        return

    if server["id"] not in usage_cache:
        try:
            usage_cache[server["id"]] = await wg.fetch_usage(server)
        except Exception as e:
            logger.warning(f"WG usage fetch failed for server {server['id']}: {e}")
            usage_cache[server["id"]] = None
    usage_map = usage_cache[server["id"]]

    used = config["used_bytes"] or 0
    if usage_map:
        peer = usage_map.get(config["wg_public_key"]) or {}
        rx = peer.get("rx", 0)
        tx = peer.get("tx", 0)
        total_now = rx + tx
        prev_total = (config["wg_last_rx"] or 0) + (config["wg_last_tx"] or 0)
        if total_now >= prev_total:
            used = used + (total_now - prev_total)
        else:
            # Router/peer counter reset — treat current counter as fresh usage.
            used = used + total_now
        try:
            await db.update_wg_usage(config["id"], rx, tx, used)
        except Exception as e:
            logger.warning(f"WG usage save failed for config {config['id']}: {e}")

    limit_bytes = (config["traffic_gb"] or 0) * ONE_GB
    expired = bool(config["expire_date"]) and config["expire_date"] <= now
    exhausted = limit_bytes > 0 and used >= limit_bytes

    if expired or exhausted:
        try:
            await wg.set_peer_enabled(
                server, False,
                public_key=config["wg_public_key"],
                peer_id=config["wg_peer_id"],
                client_ip=config["wg_client_ip"],
            )
            logger.info(f"Disabled WG peer for config {config['id']} (expired={expired}, exhausted={exhausted})")
        except Exception as e:
            logger.warning(f"Failed to disable WG peer for config {config['id']}: {e}")

    if not config["reminder_time_sent"] and config["expire_date"]:
        seconds_left = (config["expire_date"] - now).total_seconds()
        if 0 < seconds_left <= 24 * 3600:
            await _send_time_reminder(bot, config)

    if not config["reminder_traffic_sent"] and limit_bytes > 0:
        remaining = max(0, limit_bytes - used)
        if 0 <= remaining <= ONE_GB:
            await _send_traffic_reminder(bot, config)


async def _check_once(bot: Bot):
    configs = await db.get_all_active_configs()
    if not configs:
        return

    master = await db.get_master_server("v2ray")
    xui = XUIClient(master["url"], api_token=master["api_token"]) if master else None
    now = datetime.now()

    server_cache = {}
    usage_cache = {}

    for c in configs:
        try:
            if (c.get("service_type") or "v2ray") == "wireguard":
                await _check_wg_config(bot, c, now, server_cache, usage_cache)
            else:
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


def start_reminders(bot: Bot):
    logger.info("Reminder scheduler started")
    return asyncio.create_task(_reminder_loop(bot))
