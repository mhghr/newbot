from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError
import asyncio
import logging

from bot.database import db
from bot.keyboards.inline import (
    plans_keyboard, cancel_keyboard,
    order_approval_keyboard, main_menu_keyboard
)
from bot.config import ADMIN_IDS
from bot.middlewares.membership import check_membership

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


class BuyStates(StatesGroup):
    waiting_receipt = State()


async def show_payment_and_wait(callback: CallbackQuery, state: FSMContext, plan, renew_config_id=None):
    await state.update_data(plan_id=plan["id"], renew_config_id=renew_config_id)

    card_number = await db.get_setting("card_number", "تنظیم نشده")
    card_holder = await db.get_setting("card_holder", "تنظیم نشده")
    title = "🔄 تمدید اشتراک" if renew_config_id else "💳 پرداخت"

    await _retry(lambda: callback.message.edit_text(
        f"{title}\n\n"
        f"💰 مبلغ قابل پرداخت برای «{plan['name']}»: **{plan['price']:,} تومان**\n\n"
        f"💳 شماره کارت: `{card_number}`\n"
        f"👤 صاحب کارت: {card_holder}\n\n"
        "لطفا بعد از واریز، تصویر فیش واریزی را در همین مرحله ارسال کنید ⬇️",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    ))
    await state.set_state(BuyStates.waiting_receipt)


@router.callback_query(F.data == "main:buy")
async def buy_config(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return

    plans = await db.get_active_plans()
    if not plans:
        await callback.answer("❌ در حال حاضر پلنی تعریف نشده است.", show_alert=True)
        return

    await _retry(lambda: callback.message.edit_text(
        "📦 پلن مورد نظر خود را انتخاب کنید:",
        reply_markup=plans_keyboard(plans)
    ))
    await callback.answer()


@router.callback_query(F.data.startswith("buy_plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return

    servers = await db.get_active_servers(plan.get("service_type") or "v2ray")
    if not servers:
        await callback.answer("❌ سروری فعال برای این نوع سرویس نیست!", show_alert=True)
        return

    await show_payment_and_wait(callback, state, plan)
    await callback.answer()


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    plan_id = data.get("plan_id")
    renew_config_id = data.get("renew_config_id")

    user = await db.get_user_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❌ خطا! لطفا /start بزنید.")
        await state.clear()
        return

    photo_id = message.photo[-1].file_id

    plan = await db.get_plan(plan_id) if plan_id else None
    order_id = await db.create_order(
        user_id=user["id"],
        plan_id=plan_id,
        receipt_photo_id=photo_id,
        renew_config_id=renew_config_id,
        service_type=(plan["service_type"] if plan else "v2ray") or "v2ray",
    )
    await state.clear()

    order = await db.get_order(order_id)
    order_kind = "🔄 تمدید" if renew_config_id else "🆕 سفارش جدید"

    if not ADMIN_IDS:
        logger.error("ADMIN_IDS is empty - receipt cannot be sent to any admin")

    sent = 0
    for admin_id in ADMIN_IDS:
        try:
            await _retry(lambda aid=admin_id: bot.send_photo(
                chat_id=aid,
                photo=photo_id,
                caption=(
                    f"\u200f{order_kind} #{order_id}\n\n"
                    f"\u200f👤 کاربر: {order['first_name']} (@{order['username'] or 'ندارد'})\n"
                    f"\u200f🆔 آیدی: {order['telegram_id']}\n"
                    f"\u200f📦 پلن: {order['plan_name']}\n"
                    f"\u200f💰 مبلغ: {order['price']:,} تومان\n"
                    f"\u200f🌍 لوکیشن: همه سرورهای فعال"
                ),
                reply_markup=order_approval_keyboard(order_id),
            ))
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send receipt to admin {admin_id}: {type(e).__name__}: {e}")

    try:
        if sent > 0:
            await _retry(lambda: message.answer(
                f"✅ رسید شما ثبت شد!\n"
                f"شماره سفارش: #{order_id}\n\n"
                "پس از بررسی توسط ادمین، کانفیگ برای شما ارسال خواهد شد.",
                reply_markup=main_menu_keyboard(message.from_user.id)
            ))
        else:
            logger.error(
                f"Order #{order_id} receipt reached 0 admins. "
                "Make sure each admin has started the bot and the proxy is stable."
            )
            await _retry(lambda: message.answer(
                f"✅ رسید شما ثبت شد! (سفارش #{order_id})\n"
                "⚠️ ارسال به ادمین با تاخیر انجام می‌شود. لطفا منتظر بمانید.",
                reply_markup=main_menu_keyboard(message.from_user.id)
            ))
    except Exception as e:
        logger.error(f"Failed to confirm receipt to user {message.from_user.id}: {e}")


@router.message(BuyStates.waiting_receipt)
async def invalid_receipt(message: Message):
    await message.answer("⚠️ لطفا **تصویر رسید** را ارسال کنید.", parse_mode="Markdown")


@router.callback_query(F.data == "cancel_buy")
async def cancel_buy(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ عملیات خرید لغو شد.",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()
