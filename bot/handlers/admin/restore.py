import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

from bot.config import ADMIN_IDS
from bot.keyboards.inline import fill_db_confirm_keyboard
from bot.services import restore

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "admin:fill_db")
async def fill_db_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    await callback.message.edit_text(
        "🔁 پر کردن دیتابیس از روی پنل‌ها\n\n"
        "تمام کلاینت‌های پنل‌های فعال خوانده شده و کاربران، کانفیگ‌ها و "
        "سفارش‌ها بازسازی/به‌روزرسانی می‌شوند.\n"
        "هیچ داده‌ی موجودی حذف نمی‌شود (ادغام).\n\n"
        "⚠️ قبل از شروع مطمئن شوید سرورها و پلن‌ها اضافه شده‌اند.",
        reply_markup=fill_db_confirm_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "fill_db:start")
async def fill_db_start(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.edit_text("⏳ در حال شروع...")
    chat_id = status_msg.chat.id
    msg_id = status_msg.message_id

    progress = []
    task = asyncio.create_task(restore.sync_from_panels(progress.append))

    last = 0
    while not task.done() or last < len(progress):
        if len(progress) > last:
            try:
                await bot.edit_message_text(
                    "\n".join(progress[-10:]),
                    chat_id=chat_id,
                    message_id=msg_id,
                )
            except Exception:
                pass
            last = len(progress)
        if task.done() and last >= len(progress):
            break
        await asyncio.sleep(1.0)

    try:
        await task
    except Exception as e:
        logger.exception("fill_db failed")
        try:
            await bot.edit_message_text(
                f"❌ خطا در پر کردن دیتابیس:\n{e}",
                chat_id=chat_id,
                message_id=msg_id,
            )
        except Exception:
            pass
