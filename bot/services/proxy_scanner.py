"""Proxy relay built on a Telethon userbot.

The bot itself is not a member of the source channels, so it cannot receive
``channel_post`` updates from them. Instead a Telethon user account
(``TG_API_ID`` / ``TG_API_HASH``, authorised once through
``scripts/telethon_login.py``) watches the channels listed in
``PROXY_SOURCE_CHANNELS`` and every live proxy link found is relayed to the
target channel through the aiogram bot.

Source channels live in ``.env``; the target channel and the enable/disable
toggle live in the database (admin panel). Relayed URLs are de-duplicated in
memory only — nothing about the relay is persisted.
"""
import asyncio
import logging
import re
import socket
from collections import deque

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import db
from bot.config import (
    TG_API_ID, TG_API_HASH, TG_SESSION, PROXY_SOURCE_CHANNELS,
)

logger = logging.getLogger(__name__)

PROXY_PATTERN = re.compile(
    r'(https?://|socks[45]?://)([\w.-]+):(\d+)', re.IGNORECASE
)

MT_PROTO_PATTERN = re.compile(
    r'tg://proxy\?server=([\w.-]+)&port=(\d+)', re.IGNORECASE
)

# URLs already relayed during this process lifetime. Intentionally in memory:
# nothing about the relay is stored in the database. A bounded deque lets old
# entries fall off so the set cannot grow forever.
_sent_urls: set[str] = set()
_sent_order: deque[str] = deque()
_SENT_CAP = 20000

# Telethon client, set once start_proxy_userbot() connects.
_client = None


def _mark_sent(url: str) -> None:
    if url in _sent_urls:
        return
    _sent_urls.add(url)
    _sent_order.append(url)
    while len(_sent_order) > _SENT_CAP:
        _sent_urls.discard(_sent_order.popleft())


def _extract_proxy_urls(text: str) -> list[str]:
    urls = []
    for m in PROXY_PATTERN.finditer(text):
        urls.append(m.group(0).rstrip(".,;"))
    for m in MT_PROTO_PATTERN.finditer(text):
        urls.append(m.group(0))
    return urls


def _short_label(url: str) -> str:
    match = PROXY_PATTERN.search(url)
    if match:
        host = match.group(2)
        port = match.group(3)
        return f"{host}:{port}"
    return url[:40]


def _test_proxy(url: str) -> bool:
    match = PROXY_PATTERN.search(url)
    if match:
        host = match.group(2)
        port = int(match.group(3))
    else:
        mt = MT_PROTO_PATTERN.search(url)
        if not mt:
            return False
        host = mt.group(1)
        port = int(mt.group(2))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4.0)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


async def _test_urls(urls: list[str]) -> list[bool]:
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _test_proxy, u) for u in urls]
    return await asyncio.gather(*tasks)


async def _relay_text(bot: Bot, text: str) -> None:
    """Extract, verify and relay every new proxy found in ``text``."""
    if not text:
        return

    enabled = await db.get_setting("proxy_auto_enabled", "1")
    if enabled != "1":
        return

    urls = _extract_proxy_urls(text)
    # De-duplicate within the message and against what we already relayed.
    new_urls = [
        u for u in dict.fromkeys(urls) if u not in _sent_urls
    ]
    if not new_urls:
        return

    target = await db.get_proxy_target()
    if not target:
        return

    results = await _test_urls(new_urls)
    for url, ok in zip(new_urls, results):
        if not ok:
            continue
        label = _short_label(url)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔗 {label}", url=url)]
        ])
        try:
            await bot.send_message(
                chat_id=target,
                text="🔄 پروکسی جدید",
                reply_markup=keyboard,
                disable_notification=True,
            )
            _mark_sent(url)
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"send proxy to target failed: {type(e).__name__}: {e}")


async def start_proxy_userbot(bot: Bot) -> None:
    """Connect the Telethon userbot and relay proxies until disconnected.

    Safe to run as a background task: if Telethon is not configured or the
    session is missing/unauthorised it logs and returns without crashing the bot.
    """
    global _client

    if not (TG_API_ID and TG_API_HASH and PROXY_SOURCE_CHANNELS):
        logger.info(
            "Proxy userbot disabled "
            "(set TG_API_ID, TG_API_HASH and PROXY_SOURCE_CHANNELS to enable)"
        )
        return

    try:
        from telethon import TelegramClient, events
    except ImportError:
        logger.error("telethon is not installed; proxy userbot disabled")
        return

    client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)
    try:
        await client.connect()
    except Exception as e:
        logger.error(f"proxy userbot connect failed: {type(e).__name__}: {e}")
        return

    if not await client.is_user_authorized():
        logger.error(
            "proxy userbot session is not authorised; run "
            "'python scripts/telethon_login.py request' then signin"
        )
        await client.disconnect()
        return

    resolved = []
    for source in PROXY_SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(source)
        except Exception as e:
            logger.warning(f"proxy source {source} is not resolvable: {e}")
            continue
        await _ensure_joined(client, entity)
        resolved.append(entity)

    if not resolved:
        logger.error("no proxy source channel could be resolved; userbot disabled")
        await client.disconnect()
        return

    @client.on(events.NewMessage(chats=resolved))
    async def _on_new(event):
        try:
            await _relay_text(bot, event.message.message or "")
        except Exception as e:
            logger.warning(f"proxy relay failed: {type(e).__name__}: {e}")

    _client = client
    logger.info("proxy userbot watching %d source channel(s)", len(resolved))
    await client.run_until_disconnected()


async def _ensure_joined(client, entity) -> None:
    """Join the channel if the user account is not already a participant."""
    try:
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.errors import UserAlreadyParticipantError
    except ImportError:
        return
    try:
        await client(JoinChannelRequest(entity))
        logger.info(f"proxy userbot joined source channel {getattr(entity, 'id', entity)}")
    except UserAlreadyParticipantError:
        pass
    except Exception as e:
        logger.warning(f"could not join source channel {getattr(entity, 'id', entity)}: {e}")


async def test_scan(bot: Bot, admin_id: int) -> None:
    """Report the userbot/source/target status to an admin."""
    if not PROXY_SOURCE_CHANNELS:
        await bot.send_message(
            chat_id=admin_id,
            text="❌ هیچ کانال منبعی در .env تنظیم نشده (PROXY_SOURCE_CHANNELS).",
        )
        return

    if _client is None or not _client.is_connected():
        await bot.send_message(
            chat_id=admin_id,
            text="❌ یوزربات متصل نیست. TG_API_ID/TG_API_HASH و فایل session را بررسی کنید.",
        )
        return

    await bot.send_message(
        chat_id=admin_id,
        text=f"🧪 بررسی {len(PROXY_SOURCE_CHANNELS)} کانال منبع...",
    )

    try:
        me = await _client.get_me()
    except Exception:
        me = None

    for source in PROXY_SOURCE_CHANNELS:
        try:
            entity = await _client.get_entity(source)
        except Exception as e:
            await bot.send_message(
                chat_id=admin_id,
                text=f"❌ {source}: پیدا نشد یا در دسترس نیست ({type(e).__name__})",
            )
            continue

        member = False
        try:
            from telethon.tl.functions.channels import GetParticipantRequest
            await _client(GetParticipantRequest(entity, me))
            member = True
        except Exception:
            member = False

        if member:
            await bot.send_message(
                chat_id=admin_id,
                text=f"✅ {source}: یوزربات عضو است — پست‌های جدید دریافت می‌شوند",
            )
        else:
            await bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ {source}: یوزربات عضو نیست — پست‌ها دریافت نمی‌شوند",
            )

    target = await db.get_proxy_target()
    enabled = await db.get_setting("proxy_auto_enabled", "1")
    status = "✅ فعال" if enabled == "1" else "⛔️ غیرفعال"
    await bot.send_message(
        chat_id=admin_id,
        text=(
            f"🧪 پایان بررسی.\n"
            f"🎯 کانال مقصد: {target or '(تنظیم نشده)'}\n"
            f"⚙️ ارسال خودکار: {status}"
        ),
    )
