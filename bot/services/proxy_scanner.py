import asyncio
import logging
import re
from datetime import datetime, time

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import db

logger = logging.getLogger(__name__)

SCAN_TIMES = [time(8, 0), time(20, 0)]
CHECK_INTERVAL = 60

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


async def _scan_source(bot: Bot, source, target_channel: str):
    channel = source["channel"]
    try:
        msgs = await bot.get_chat_history(chat_id=channel, limit=5)
    except Exception as e:
        logger.warning(f"cannot read source channel {channel}: {type(e).__name__}: {e}")
        return 0

    all_urls = []
    for m in reversed(msgs):
        text = m.text or m.caption or ""
        all_urls.extend(_extract_proxy_urls(text))

    if not all_urls:
        return 0

    sent_set = await db.are_proxies_sent(all_urls)
    new_urls = [u for u in all_urls if u not in sent_set]
    if not new_urls:
        return 0

    import asyncio as _asyncio
    import socket as _socket

    def _test(url: str) -> bool:
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
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.settimeout(4.0)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            return False

    loop = _asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _test, u) for u in new_urls]
    results = await _asyncio.gather(*tasks)

    sent_count = 0
    for url, ok in zip(new_urls, results):
        if not ok:
            continue
        try:
            label = _short_label(url)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🔗 {label}", url=url)]
            ])
            await bot.send_message(
                chat_id=target_channel,
                text="🔄 پروکسی جدید",
                reply_markup=keyboard,
                disable_notification=True,
            )
            await db.add_sent_proxy(url)
            sent_count += 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"send proxy to target failed: {type(e).__name__}: {e}")

    return sent_count


async def _proxy_loop(bot: Bot, target_channel: str):
    while True:
        try:
            now = datetime.now()
            current_time = now.time()
            should_run = any(
                abs((current_time.hour * 60 + current_time.minute) - (t.hour * 60 + t.minute)) <= 1
                for t in SCAN_TIMES
            )
            if not should_run:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            sources = await db.get_active_proxy_sources()
            total = 0
            for s in sources:
                count = await _scan_source(bot, s, target_channel)
                total += count
                await asyncio.sleep(3)
            logger.info(f"proxy scan done: {total} new proxies sent")
            await asyncio.sleep(120)
        except Exception as e:
            logger.error(f"proxy loop error: {type(e).__name__}: {e}")
            await asyncio.sleep(60)


def start_proxy_scanner(bot: Bot, target_channel: str):
    logger.info(f"Proxy scanner started (8AM/8PM) -> target: {target_channel}")
    return asyncio.create_task(_proxy_loop(bot, target_channel))


async def test_scan(bot: Bot, admin_id: int):
    sources = await db.get_active_proxy_sources()
    if not sources:
        await bot.send_message(chat_id=admin_id, text="❌ هیچ کانال منبع فعالی وجود ندارد.")
        return

    await bot.send_message(chat_id=admin_id, text=f"🧪 شروع تست اسکن ({len(sources)} کانال)...")

    total = 0
    for s in sources:
        channel = s["channel"]
        try:
            msgs = await bot.get_chat_history(chat_id=channel, limit=5)
        except Exception as e:
            await bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ خطا در خواندن {channel}: {type(e).__name__}"
            )
            continue

        all_urls = []
        for m in reversed(msgs):
            text = m.text or m.caption or ""
            all_urls.extend(_extract_proxy_urls(text))

        if not all_urls:
            await bot.send_message(
                chat_id=admin_id,
                text=f"📡 {channel}: پروکسی یافت نشد"
            )
            continue

        sent_set = await db.are_proxies_sent(all_urls)
        new_urls = [u for u in all_urls if u not in sent_set]

        import asyncio as _asyncio
        import socket as _socket

        def _test(url: str) -> bool:
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
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.settimeout(4.0)
                s.connect((host, port))
                s.close()
                return True
            except Exception:
                return False

        loop = _asyncio.get_running_loop()
        tasks = [loop.run_in_executor(None, _test, u) for u in new_urls]
        results = await _asyncio.gather(*tasks)

        working = []
        total_all = len(all_urls)
        new_count = len(new_urls)
        for url, ok in zip(new_urls, results):
            if ok:
                working.append(url)

        already = total_all - new_count
        failed = new_count - len(working)

        status = (
            f"📡 {channel}:\n"
            f"   کل: {total_all} | تکراری: {already} | جدید: {new_count}\n"
            f"   ✅ متصل: {len(working)} | ❌ ناموفق: {failed}"
        )
        await bot.send_message(chat_id=admin_id, text=status)

        for url in working:
            label = _short_label(url)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🔗 {label}", url=url)]
            ])
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"🔄 تست پروکسی",
                    reply_markup=keyboard,
                )
                total += 1
            except Exception as e:
                logger.warning(f"test send to admin failed: {type(e).__name__}: {e}")

        await asyncio.sleep(1)

    await bot.send_message(
        chat_id=admin_id,
        text=f"🧪 تست پایان یافت. {total} پروکسی سالم یافت شد (بدون ذخیره‌سازی)."
    )
