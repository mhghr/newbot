import logging
import re
import socket
import asyncio

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import cancel_keyboard

logger = logging.getLogger(__name__)

router = Router()

PROXY_PATTERN = re.compile(
    r'(https?://|socks[45]?://)([\w.-]+):(\d+)', re.IGNORECASE
)


class ProxyApproveStates(StatesGroup):
    waiting_response = State()


def _test_proxy(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


async def _test_proxies_in_text(text: str) -> bool:
    for m in PROXY_PATTERN.finditer(text):
        host = m.group(2)
        port = int(m.group(3))
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, _test_proxy, host, port, 3.0)
        if ok:
            return True
    return False


def _approve_keyboard(pid: int) -> "InlineKeyboardMarkup":
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید و ارسال", callback_data=f"proxy_ok:{pid}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"proxy_no:{pid}"),
        ]
    ])


@router.channel_post()
async def proxy_channel_post(message: Message, bot: Bot):
    chat_id = str(message.chat.id)
    text = message.text or message.caption or ""

    if not PROXY_PATTERN.search(text):
        return

    sources = await db.get_active_proxy_sources()
    matched = False
    for s in sources:
        src = s["channel"]
        if src.lstrip("@") == chat_id or src == chat_id:
            matched = True
            break
    if not matched:
        return

    ok = await _test_proxies_in_text(text)
    if not ok:
        logger.info(f"proxy test failed for msg {message.message_id}, skipped")
        return

    target = await db.get_setting("proxy_target_channel", "")
    if not target:
        return

    pid = await db.create_proxy_pending(
        text=text,
        photo_id=message.photo[-1].file_id if message.photo else "",
        doc_id=message.document.file_id if message.document else "",
        target_channel=target,
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"🔄 پروکسی جدید برای تأیید #{pid}\n\n{text[:3500]}",
                reply_markup=_approve_keyboard(pid),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning(f"proxy notify admin {admin_id}: {type(e).__name__}: {e}")


@router.callback_query(F.data.startswith("proxy_ok:"))
async def proxy_approve(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    pid = int(callback.data.split(":")[1])
    p = await db.get_proxy_pending(pid)
    if not p or p["status"] != "pending":
        await callback.answer("⚠️ قبلاً بررسی شده!", show_alert=True)
        return

    text = p["text"]
    target = p["target_channel"]

    try:
        if p["photo_id"]:
            await bot.send_photo(chat_id=target, photo=p["photo_id"], caption=text)
        elif p["doc_id"]:
            await bot.send_document(chat_id=target, document=p["doc_id"], caption=text)
        elif text:
            await bot.send_message(chat_id=target, text=text, link_preview_options={"is_disabled": True})
        await db.delete_proxy_pending(pid)
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ تأیید و ارسال شد"
        )
        await callback.answer("ارسال شد!")
    except Exception as e:
        await callback.answer(f"❌ خطا: {e}", show_alert=True)


@router.callback_query(F.data.startswith("proxy_no:"))
async def proxy_reject(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    pid = int(callback.data.split(":")[1])
    await db.delete_proxy_pending(pid)
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ رد شد"
    )
    await callback.answer("رد شد!")
