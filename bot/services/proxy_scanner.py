import asyncio
import logging
import re
from datetime import datetime

from aiogram import Bot

from bot.database import db

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 3600  # 1 hour

PROXY_PATTERN = re.compile(
    r'(https?://|socks[45]?://)([\w.-]+):(\d+)', re.IGNORECASE
)


async def _scan_source(bot: Bot, source, target_channel: str):
    sid = source["id"]
    channel = source["channel"]
    last_id = source["last_scan_id"] or 0

    try:
        updates = await bot.get_updates(offset=0, timeout=1, allowed_updates=["channel_post"])
        await asyncio.sleep(0.5)

        from aiogram.methods import GetUpdates
    except Exception:
        pass

    try:
        msgs = await bot.get_chat_history(chat_id=channel, limit=50)
    except Exception as e:
        logger.warning(f"cannot read source channel {channel}: {type(e).__name__}: {e}")
        return

    new_last = last_id
    count = 0
    for m in reversed(msgs):
        if m.message_id <= last_id:
            continue
        text = m.text or m.caption or ""
        if not PROXY_PATTERN.search(text):
            if m.message_id > new_last:
                new_last = m.message_id
            continue

        try:
            await _forward(bot, m, text, target_channel)
            count += 1
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"forward msg {m.message_id} from {channel} failed: {type(e).__name__}: {e}")

        if m.message_id > new_last:
            new_last = m.message_id

    if new_last > last_id:
        await db.update_proxy_source_scan(sid, new_last)
        logger.info(f"proxy scan {channel}: {count} forwarded")


async def _forward(bot: Bot, msg, text: str, target: str):
    if msg.photo:
        await bot.send_photo(
            chat_id=target, photo=msg.photo[-1].file_id,
            caption=text, disable_notification=True,
        )
    elif msg.document:
        await bot.send_document(
            chat_id=target, document=msg.document.file_id,
            caption=text, disable_notification=True,
        )
    elif msg.text:
        await bot.send_message(
            chat_id=target, text=text, disable_notification=True,
            link_preview_options={"is_disabled": True},
        )
    else:
        return


async def _proxy_loop(bot: Bot, target_channel: str):
    while True:
        try:
            sources = await db.get_active_proxy_sources()
            for s in sources:
                await _scan_source(bot, s, target_channel)
                await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"proxy loop error: {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


def start_proxy_scanner(bot: Bot, target_channel: str):
    logger.info(f"Proxy scanner started → target: {target_channel}")
    return asyncio.create_task(_proxy_loop(bot, target_channel))
