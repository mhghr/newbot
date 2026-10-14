from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from datetime import datetime
from urllib.parse import quote

from bot.database import db
from bot.keyboards.inline import (
    my_configs_keyboard, account_detail_keyboard,
    renew_choice_keyboard, renew_plans_keyboard, back_to_menu_keyboard,
    main_menu_keyboard,
)
from bot.middlewares.membership import check_membership
from bot.services.xui import XUIClient
from bot.services import account_view
from bot.utils.jalali import to_jalali
from bot.utils.helpers import format_gb
from html import escape as _esc

router = Router()

ONE_GB = 1024 * 1024 * 1024

DEFAULT_RENEW_DISABLED = "⛔️ تمدید موقتاً غیرفعال است. لطفاً بعداً تلاش کنید."


async def show_renew_disabled(callback: CallbackQuery):
    """Show the admin-configured (or default) notice when renew is disabled."""
    message = await db.get_setting("renew_disabled_message", "")
    text = (message or "").strip() or DEFAULT_RENEW_DISABLED
    keyboard = main_menu_keyboard(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


async def _get_owned_config(telegram_id: int, config_id: int):
    configs = await db.get_configs_by_telegram_id(telegram_id)
    for c in configs:
        if c["id"] == config_id:
            return c
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
            "📋 شما هیچ اکانت فعالی ندارید.\nاز بخش «خرید» اقدام کنید.",
            reply_markup=back_to_menu_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📋 اکانت‌های شما:\nروی هرکدام بزنید تا جزئیاتش را ببینید.",
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

    service = config.get("service_type") or "v2ray"
    text = await account_view.build_account_text(config)
    renew_enabled = await db.is_renew_enabled()
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=account_detail_keyboard(config["id"], service, show_renew=renew_enabled),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("showconfig:"))
async def show_config(callback: CallbackQuery, bot: Bot):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    await callback.answer("⏳ در حال ارسال کانفیگ...")
    await account_view.send_account_config(bot, callback.from_user.id, config)


@router.callback_query(F.data == "cfg_noop")
async def cfg_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("qr:"))
async def show_qr(callback: CallbackQuery, bot: Bot):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ اکانت یافت نشد!", show_alert=True)
        return
    sub_url = config.get("sub_url") or config.get("config_link")
    if not sub_url:
        await callback.answer("❌ لینک اشتراک یافت نشد!", show_alert=True)
        return
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=500x500&qzone=2&margin=10&data={quote(sub_url, safe='')}"
    )
    try:
        await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=qr_url,
            caption="📷 QR کد اشتراک V2Ray\nبا اپلیکیشن خود اسکن کنید.",
        )
    except Exception:
        await callback.answer("❌ ارسال تصویر ناموفق بود.", show_alert=True)
        return
    await callback.answer("✅ ارسال شد!")


@router.callback_query(F.data.startswith("renew:"))
async def renew_start(callback: CallbackQuery):
    if not await db.is_renew_enabled():
        await show_renew_disabled(callback)
        return
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
    if not await db.is_renew_enabled():
        await show_renew_disabled(callback)
        return
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
    if not await db.is_renew_enabled():
        await show_renew_disabled(callback)
        return
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
    if not await db.is_renew_enabled():
        await show_renew_disabled(callback)
        return
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
