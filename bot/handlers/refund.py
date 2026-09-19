from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError
from datetime import datetime
import asyncio
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    refund_configs_keyboard, refund_approval_keyboard, refund_upload_keyboard,
    back_to_menu_keyboard, main_menu_keyboard, cancel_keyboard
)
from bot.middlewares.membership import check_membership
from bot.services.xui import XUIClient, format_bytes
from bot.utils.jalali import to_jalali

logger = logging.getLogger(__name__)

router = Router()

_RETRYABLE = (TelegramNetworkError, ConnectionError, OSError, asyncio.TimeoutError)


async def _retry(coro_factory, attempts: int = 6, delay: float = 3.0):
    last_error = None
    for i in range(attempts):
        try:
            return await coro_factory()
        except _RETRYABLE as e:
            last_error = e
            logger.warning(f"network error (attempt {i + 1}/{attempts}): {type(e).__name__}: {e}")
            await asyncio.sleep(delay)
    if last_error:
        raise last_error


class RefundStates(StatesGroup):
    waiting_card_number = State()
    waiting_card_holder = State()
    admin_approve_response = State()
    admin_receipt_photo = State()
    admin_reject_reason = State()


async def _owned_config(telegram_id: int, config_id: int):
    configs = await db.get_configs_by_telegram_id(telegram_id)
    for c in configs:
        if c["id"] == config_id:
            return c
    return None


# ---------------- User side ----------------

@router.callback_query(F.data == "main:refund")
async def refund_menu(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return

    configs = await db.get_configs_by_telegram_id(callback.from_user.id)
    if not configs:
        await callback.message.edit_text(
            "💵 عودت وجه\n\nشما هیچ کانفیگ فعالی ندارید.",
            reply_markup=back_to_menu_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "💵 عودت وجه\n\n"
        "اگر سرویسی که خریداری کرده‌اید نیاز شما را برطرف نمی‌کند، می‌توانید درخواست عودت وجه ثبت کنید.\n\n"
        "برای این کار کانفیگی که مدنظر شما برای بازگشت وجه است را از لیست زیر انتخاب کنید:",
        reply_markup=refund_configs_keyboard(configs)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("refund_cfg:"))
async def refund_choose_config(callback: CallbackQuery, state: FSMContext):
    config_id = int(callback.data.split(":")[1])
    config = await _owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    if await db.has_active_refund(config_id):
        await callback.answer(
            "⚠️ برای این کانفیگ قبلاً درخواست عودت وجه ثبت کرده‌اید.",
            show_alert=True
        )
        return

    await state.update_data(refund_config_id=config_id)
    await callback.message.edit_text(
        "💳 لطفاً شماره کارت خود را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(RefundStates.waiting_card_number)
    await callback.answer()


@router.message(RefundStates.waiting_card_number)
async def refund_card_number(message: Message, state: FSMContext):
    card = message.text.strip()
    if len(card) < 6:
        await message.answer("⚠️ شماره کارت نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return
    await state.update_data(card_number=card)
    await message.answer("👤 نام صاحب حساب را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(RefundStates.waiting_card_holder)


@router.message(RefundStates.waiting_card_holder)
async def refund_card_holder(message: Message, state: FSMContext, bot: Bot):
    holder = message.text.strip()
    if not holder:
        await message.answer("⚠️ نام نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return

    data = await state.get_data()
    config_id = data.get("refund_config_id")
    card_number = data.get("card_number")
    await state.clear()

    user = await db.get_user_by_telegram_id(message.from_user.id)
    config = await _owned_config(message.from_user.id, config_id)
    if not user or not config:
        await message.answer("❌ خطا! لطفا دوباره تلاش کنید.", reply_markup=main_menu_keyboard(message.from_user.id))
        return

    if await db.has_active_refund(config_id):
        await message.answer("⚠️ برای این کانفیگ قبلاً درخواست ثبت شده است.",
                             reply_markup=main_menu_keyboard(message.from_user.id))
        return

    refund_id = await db.create_refund(user["id"], config_id, card_number, holder)

    await message.answer(
        "✅ درخواست عودت وجه شما ثبت شد.\n"
        "پس از بررسی، نتیجه به شما اعلام می‌شود.",
        reply_markup=main_menu_keyboard(message.from_user.id)
    )

    await _notify_admins_refund(bot, refund_id)


async def _notify_admins_refund(bot: Bot, refund_id: int):
    refund = await db.get_refund(refund_id)
    if not refund:
        return

    remaining_days = 0
    if refund["expire_date"]:
        remaining_days = max(0, (refund["expire_date"] - datetime.now()).days)

    used_txt = "نامشخص"
    try:
        if (refund.get("service_type") or "v2ray") == "wireguard":
            used_txt = format_bytes(refund.get("used_bytes") or 0)
        else:
            master = await db.get_master_server("v2ray")
            if master:
                xui = XUIClient(master["url"], api_token=master["api_token"])
                traffic = await xui.get_client_traffic(refund["client_email"])
                used_txt = format_bytes(traffic["used"])
    except Exception:
        pass

    bought = to_jalali(refund["config_created_at"]) if refund["config_created_at"] else "-"

    caption = (
        f"\u200f💵 درخواست عودت وجه #{refund_id}\n\n"
        f"\u200f👤 کاربر: {refund['first_name']} (@{refund['username'] or 'ندارد'})\n"
        f"\u200f🆔 آیدی: {refund['telegram_id']}\n"
        f"\u200f🔑 کانفیگ: {refund['client_email']}\n"
        f"\u200f📦 پلن: {refund['plan_name'] or '-'}\n"
        f"\u200f🗓 تاریخ خرید: {bought}\n"
        f"\u200f📊 ترافیک مصرفی: {used_txt}\n"
        f"\u200f📅 روز باقیمانده: {remaining_days} روز\n\n"
        f"\u200f💳 شماره کارت: {refund['card_number']}\n"
        f"\u200f👤 صاحب حساب: {refund['card_holder']}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await _retry(lambda aid=admin_id: bot.send_message(
                chat_id=aid, text=caption, reply_markup=refund_approval_keyboard(refund_id)
            ))
        except Exception as e:
            logger.error(f"Failed to send refund {refund_id} to admin {admin_id}: {type(e).__name__}: {e}")


# ---------------- Admin side ----------------

@router.callback_query(F.data.startswith("refund_ok:"))
async def refund_approve(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    refund_id = int(callback.data.split(":")[1])
    refund = await db.get_refund(refund_id)
    if not refund:
        await callback.answer("❌ یافت نشد!", show_alert=True)
        return
    if refund["status"] != "pending":
        await callback.answer(f"⚠️ این درخواست قبلاً {refund['status']} شده!", show_alert=True)
        return

    await state.update_data(refund_id=refund_id)
    await callback.message.answer("📝 پاسخ درخواست را بنویسید:", reply_markup=cancel_keyboard())
    await state.set_state(RefundStates.admin_approve_response)
    await callback.answer()


@router.message(RefundStates.admin_approve_response)
async def refund_approve_response(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    refund_id = data.get("refund_id")
    await db.update_refund(refund_id, admin_response=message.text)
    await state.clear()
    await message.answer(
        "✅ پاسخ ذخیره شد. حالا فیش واریز را آپلود کنید:",
        reply_markup=refund_upload_keyboard(refund_id)
    )


@router.callback_query(F.data.startswith("refund_upload:"))
async def refund_upload_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    refund_id = int(callback.data.split(":")[1])
    await state.update_data(refund_id=refund_id)
    await callback.message.answer("📎 تصویر فیش واریز را ارسال کنید:", reply_markup=cancel_keyboard())
    await state.set_state(RefundStates.admin_receipt_photo)
    await callback.answer()


@router.message(RefundStates.admin_receipt_photo, F.photo)
async def refund_receipt_photo(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    refund_id = data.get("refund_id")
    photo_id = message.photo[-1].file_id
    await state.clear()

    await db.update_refund(refund_id, status="approved", receipt_photo_id=photo_id)
    refund = await db.get_refund(refund_id)

    text = (
        "✅ درخواست عودت وجه شما تایید شد.\n\n"
        f"{refund['admin_response'] or ''}"
    )
    try:
        await _retry(lambda: bot.send_photo(
            chat_id=refund["telegram_id"], photo=photo_id, caption=text
        ))
        await message.answer("✅ پاسخ و فیش برای کاربر ارسال شد.", reply_markup=main_menu_keyboard(message.from_user.id))
    except Exception as e:
        logger.error(f"Failed to send refund result to user: {type(e).__name__}: {e}")
        await message.answer("⚠️ ارسال به کاربر ناموفق بود، بعداً دوباره تلاش می‌شود.")


@router.message(RefundStates.admin_receipt_photo)
async def refund_receipt_invalid(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("⚠️ لطفاً تصویر فیش را ارسال کنید.", reply_markup=cancel_keyboard())


@router.callback_query(F.data.startswith("refund_no:"))
async def refund_reject(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    refund_id = int(callback.data.split(":")[1])
    refund = await db.get_refund(refund_id)
    if not refund:
        await callback.answer("❌ یافت نشد!", show_alert=True)
        return
    if refund["status"] != "pending":
        await callback.answer(f"⚠️ این درخواست قبلاً {refund['status']} شده!", show_alert=True)
        return

    await state.update_data(refund_id=refund_id)
    await callback.message.answer("📝 دلیل عدم تایید را بنویسید:", reply_markup=cancel_keyboard())
    await state.set_state(RefundStates.admin_reject_reason)
    await callback.answer()


@router.message(RefundStates.admin_reject_reason)
async def refund_reject_reason(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    refund_id = data.get("refund_id")
    reason = message.text
    await state.clear()

    await db.update_refund(refund_id, status="rejected", admin_response=reason)
    refund = await db.get_refund(refund_id)

    try:
        await _retry(lambda: bot.send_message(
            chat_id=refund["telegram_id"],
            text=f"❌ درخواست عودت وجه شما تایید نشد.\n\nدلیل: {reason}"
        ))
        await message.answer("پاسخ برای کاربر ارسال شد.", reply_markup=main_menu_keyboard(message.from_user.id))
    except Exception as e:
        logger.error(f"Failed to send refund rejection to user: {type(e).__name__}: {e}")
        await message.answer("⚠️ ارسال به کاربر ناموفق بود.")
