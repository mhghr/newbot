from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from datetime import datetime

from bot.database import db
from bot.keyboards.inline import (
    my_configs_keyboard, config_detail_keyboard,
    renew_choice_keyboard, renew_plans_keyboard, back_to_menu_keyboard
)
from bot.middlewares.membership import check_membership
from bot.services.xui import XUIClient, format_bytes
from bot.utils.jalali import to_jalali

router = Router()


async def _get_owned_config(telegram_id: int, config_id: int):
    configs = await db.get_configs_by_telegram_id(telegram_id)
    for c in configs:
        if c["id"] == config_id:
            return c
    return None


async def _config_traffic(config):
    master = await db.get_master_server()
    if not master:
        return None
    try:
        xui = XUIClient(master["url"], api_token=master["api_token"])
        return await xui.get_client_traffic(config["client_email"])
    except Exception:
        return None


@router.callback_query(F.data == "main:my_configs")
async def my_configs(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return

    configs = await db.get_configs_by_telegram_id(callback.from_user.id)
    if not configs:
        await callback.message.edit_text(
            "📋 شما هیچ کانفیگ فعالی ندارید.\nاز بخش «خرید کانفیگ» اقدام کنید.",
            reply_markup=back_to_menu_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📋 کانفیگ‌های شما:\nروی هرکدام بزنید تا جزئیاتش را ببینید.",
        reply_markup=my_configs_keyboard(configs)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfg:"))
async def view_config(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    remaining_days = 0
    if config["expire_date"]:
        remaining_days = max(0, (config["expire_date"] - datetime.now()).days)

    traffic = await _config_traffic(config)
    if traffic:
        if traffic.get("total", 0) > 0:
            traffic_info = (
                f"📊 مصرف: {format_bytes(traffic['used'])} از {format_bytes(traffic['total'])}\n"
                f"📊 باقیمانده: {format_bytes(traffic['remaining'])}\n"
            )
        else:
            traffic_info = f"📊 مصرف: {format_bytes(traffic['used'])} (نامحدود)\n"
    else:
        traffic_info = "📊 ترافیک: در دسترس نیست\n"

    text = (
        f"🔑 کانفیگ #{config['id']}\n\n"
        f"📦 پلن: {config.get('plan_name') or '-'}\n"
        f"📅 تاریخ انقضا: {to_jalali(config['expire_date']) if config['expire_date'] else 'نامحدود'}\n"
        f"📅 روز باقیمانده: {remaining_days} روز\n"
        f"{traffic_info}\n"
        f"🔗 لینک اشتراک:\n`{config['sub_url']}`"
    )

    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=config_detail_keyboard(config_id, True)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew:"))
async def renew_start(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    await callback.message.edit_text(
        "🔄 تمدید اشتراک\n\nمی‌خواهید با همان پلن فعلی تمدید کنید یا پلن را تغییر دهید؟",
        reply_markup=renew_choice_keyboard(config_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_same:"))
async def renew_same(callback: CallbackQuery, state: FSMContext):
    from bot.handlers.buy import show_payment_and_wait
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    plan = await db.get_plan(config["plan_id"]) if config["plan_id"] else None
    if not plan:
        await callback.answer("❌ پلن فعلی در دسترس نیست، «تغییر پلن» را انتخاب کنید.", show_alert=True)
        return
    await show_payment_and_wait(callback, state, plan, renew_config_id=config_id)
    await callback.answer()


@router.callback_query(F.data.startswith("renew_change:"))
async def renew_change(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    plans = await db.get_active_plans()
    if not plans:
        await callback.answer("❌ پلنی موجود نیست!", show_alert=True)
        return
    await callback.message.edit_text(
        "🔀 پلن جدید را انتخاب کنید:",
        reply_markup=renew_plans_keyboard(config_id, plans)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_plan:"))
async def renew_plan(callback: CallbackQuery, state: FSMContext):
    from bot.handlers.buy import show_payment_and_wait
    parts = callback.data.split(":")
    config_id = int(parts[1])
    plan_id = int(parts[2])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return
    await show_payment_and_wait(callback, state, plan, renew_config_id=config_id)
    await callback.answer()
