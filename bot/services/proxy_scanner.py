import asyncio
import logging
import re
import socket

from aiogram import Bot, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import db

logger = logging.getLogger(__name__)

router = Router()

PROXY_PATTERN = re.compile(
    r'(https?://|socks[45]?://)([\w.-]+):(\d+)', re.IGNORECASE
)

MT_PROTO_PATTERN = re.compile(
    r'tg://proxy\?server=([\w.-]+)&port=(\d+)', re.IGNORECASE
)


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


def _source_matches(source: str, chat) -> bool:
    s = (source or "").strip()
    if s.lstrip("-").isdigit():
        return chat.id == int(s)
    username = s.split("/")[-1].strip().lstrip("@")
    if not username:
        return False
    if chat.username:
        return username.lower() == chat.username.lower()
    return False


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


@router.channel_post()
async def on_channel_post(message: Message, bot: Bot):
    try:
        enabled = await db.get_setting("proxy_auto_enabled", "1")
        if enabled != "1":
            return

        if not message.chat:
            return

        sources = await db.get_active_proxy_sources()
        if not any(_source_matches(s["channel"], message.chat) for s in sources):
            return

        text = message.text or message.caption or ""
        urls = _extract_proxy_urls(text)
        if not urls:
            return

        target = await db.get_proxy_target()
        if not target:
            return

        sent_set = await db.are_proxies_sent(urls)
        new_urls = [u for u in urls if u not in sent_set]
        if not new_urls:
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
                await db.add_sent_proxy(url)
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"send proxy to target failed: {type(e).__name__}: {e}")
    except Exception as e:
        logger.warning(f"channel post handler failed: {type(e).__name__}: {e}")


async def test_scan(bot: Bot, admin_id: int):
    sources = await db.get_active_proxy_sources()
    if not sources:
        await bot.send_message(chat_id=admin_id, text="❌ هیچ کانال منبع فعالی وجود ندارد.")
        return

    await bot.send_message(chat_id=admin_id, text=f"🧪 بررسی کانال‌های منبع ({len(sources)} کانال)...")

    try:
        me = await bot.get_me()
        bot_id = me.id
    except Exception:
        bot_id = None

    ready = 0
    for s in sources:
        channel = s["channel"]

        try:
            chat = await bot.get_chat(chat_id=channel)
        except Exception:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"❌ {channel}: کانال پیدا نشد یا در دسترس نیست.\n"
                    "مطمئن شوید یوزرنیم درست است و بات به کانال اضافه شده."
                )
            )
            continue

        try:
            member = await bot.get_chat_member(chat_id=chat.id, user_id=bot_id)
        except Exception:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"⚠️ {channel}: بات ادمین این کانال نیست.\n"
                    "بات را در کانال به عنوان ادمین اضافه کنید تا پست‌ها دریافت شوند."
                )
            )
            continue

        if member.status == "administrator":
            ready += 1
            await bot.send_message(
                chat_id=admin_id,
                text=f"✅ {channel}: بات ادمین است — پست‌های جدید دریافت می‌شوند"
            )
        elif member.status == "creator":
            ready += 1
            await bot.send_message(
                chat_id=admin_id,
                text=f"✅ {channel}: بات صاحب کانال است — پست‌های جدید دریافت می‌شوند"
            )
        else:
            await bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ {channel}: بات عضو است ولی ادمین نیست — پست‌ها دریافت نمی‌شوند"
            )

    enabled = await db.get_setting("proxy_auto_enabled", "1")
    status = "✅ فعال" if enabled == "1" else "⛔️ غیرفعال"
    await bot.send_message(
        chat_id=admin_id,
        text=f"🧪 پایان بررسی. {ready} کانال آماده.\n⚙️ ارسال خودکار: {status}"
    )
