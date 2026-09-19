from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    cancel_keyboard, admin_menu_keyboard,
    admin_user_detail_keyboard, admin_config_detail_keyboard,
    admin_config_delete_confirm_keyboard,
)
from bot.utils.jalali import to_jalali

router = Router()

STATUS_EMOJI = {"pending": "⏳", "approved": "✅", "rejected": "❌", "processing": "⏳"}


class SearchUserStates(StatesGroup):
    waiting_query = State()


def _remaining_days(expire_date) -> int:
    if not expire_date:
        return 0
    if isinstance(expire_date, str):
        try:
            expire_date = datetime.fromisoformat(expire_date)
        except Exception:
            return 0
    return max(0, (expire_date - datetime.now()).days)


def _user_info_lines(user) -> list[str]:
    name_parts = [p for p in [user.get("first_name"), user.get("last_name")] if p]
    name = " ".join(name_parts) if name_parts else "-"
    return [
        f"🆔 آیدی: `{user['telegram_id']}`",
        f"📛 نام: {name}",
        f"👤 یوزرنیم: @{user.get('username') or 'ندارد'}",
        f"📅 تاریخ عضویت: {to_jalali(user['created_at'], with_time=True)}",
    ]


@router.callback_query(F.data == "admin:search_user")
async def search_user_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🔍 هر بخشی از آیدی عددی، یوزرنیم، نام یا ایمیل/نام اکانت کاربر را وارد کنید\n"
        "(حتی چند کاراکتر کافی است، به بزرگی و کوچکی حروف حساس نیست):",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SearchUserStates.waiting_query)
    await callback.answer()


@router.message(SearchUserStates.waiting_query)
async def search_user_result(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    query = message.text.strip().lstrip("@")
    users = await db.search_user(query)

    if not users:
        await message.answer("❌ کاربری یافت نشد.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return

    if len(users) == 1:
        user = users[0]
        configs = await db.get_configs_by_user_id(user["id"])
        info = "\n".join(_user_info_lines(user))
        text = f"👤 اطلاعات کاربر:\n\n{info}\n\n📋 کانفیگ‌ها: {len(configs)} عدد"
        await message.answer(
            text, parse_mode="Markdown",
            reply_markup=admin_user_detail_keyboard(user, configs)
        )
    else:
        buttons = []
        shown = users[:10]
        for u in shown:
            buttons.append([InlineKeyboardButton(
                text=f"{u['first_name'] or '-'} | @{u['username'] or 'N/A'} | {u['telegram_id']}",
                callback_data=f"admin:user_detail:{u['id']}"
            )])
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
        if len(users) > len(shown):
            header = f"🔍 {len(users)} کاربر یافت شد (۱۰ مورد اول نمایش داده می‌شود):"
        else:
            header = f"🔍 {len(users)} کاربر یافت شد:"
        await message.answer(
            header,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    await state.clear()


@router.callback_query(F.data.startswith("admin:user_detail:"))
async def user_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    user_id = int(callback.data.split(":")[2])

    user = await db.get_user_by_id(user_id)
    if not user:
        await callback.answer("❌ کاربر یافت نشد!", show_alert=True)
        return

    configs = await db.get_configs_by_user_id(user["id"])
    info = "\n".join(_user_info_lines(user))
    text = f"👤 اطلاعات کاربر:\n\n{info}\n\n📋 کانفیگ‌ها: {len(configs)} عدد"
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=admin_user_detail_keyboard(user, configs)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:config_detail:"))
async def config_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    config_id = int(callback.data.split(":")[2])

    config = await db.get_config(config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    remaining = _remaining_days(config.get("expire_date"))
    traffic_str = "نامحدود" if (config.get("traffic_gb") or 0) == 0 else f"{config['traffic_gb']} GB"
    expire_str = to_jalali(config["expire_date"]) if config.get("expire_date") else "نامحدود"
    service = config.get("service_type") or "v2ray"

    if service == "wireguard":
        text = (
            f"🔑 اطلاعات کانفیگ وایرگارد #{config['id']}\n\n"
            f"📦 پلن: {config.get('plan_name') or '-'}\n"
            f"📊 حجم: {traffic_str}\n"
            f"📅 تاریخ انقضا: {expire_str}\n"
            f"📅 روز باقی‌مانده: {remaining} روز\n"
            f"🌐 IP: `{config.get('wg_client_ip') or '-'}`\n"
            f"📈 مصرف: {config.get('used_bytes') or 0} بایت"
        )
    else:
        sub_link = config.get("sub_url") or config.get("config_link") or "-"
        text = (
            f"🔑 اطلاعات کانفیگ #{config['id']}\n\n"
            f"📦 پلن: {config.get('plan_name') or '-'}\n"
            f"📊 حجم: {traffic_str}\n"
            f"📅 تاریخ انقضا: {expire_str}\n"
            f"📅 روز باقی‌مانده: {remaining} روز\n"
            f"👤 کلاینت: {config.get('client_email') or '-'}\n"
            f"🔗 لینک اشتراک:\n`{sub_link}`"
        )
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=admin_config_detail_keyboard(config)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:delete_config:"))
async def delete_config_ask(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    config_id = int(callback.data.split(":")[2])

    config = await db.get_config(config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    await callback.message.edit_text(
        f"⚠️ آیا از حذف کانفیگ #{config_id} اطمینان دارید؟\n\n"
        f"👤 کلاینت: {config.get('client_email') or '-'}\n"
        f"📦 پلن: {config.get('plan_name') or '-'}\n\n"
        "این عملیات غیرقابل بازگشت است.",
        reply_markup=admin_config_delete_confirm_keyboard(config_id, config.get("user_id", 0))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:delete_config_confirm:"))
async def delete_config_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    config_id = int(callback.data.split(":")[2])

    config = await db.get_config(config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    await db.delete_config(config_id)

    user_id = config.get("user_id", 0)
    user = await db.get_user_by_id(user_id)

    if user:
        configs = await db.get_configs_by_user_id(user_id)
        info = "\n".join(_user_info_lines(user))
        text = f"✅ کانفیگ #{config_id} با موفقیت حذف شد.\n\n👤 اطلاعات کاربر:\n\n{info}\n\n📋 کانفیگ‌ها: {len(configs)} عدد"
        await callback.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=admin_user_detail_keyboard(user, configs)
        )
    else:
        await callback.message.edit_text(
            f"✅ کانفیگ #{config_id} حذف شد.",
            reply_markup=admin_menu_keyboard()
        )

    await callback.answer("✅ کانفیگ با موفقیت حذف شد.", show_alert=True)


@router.callback_query(F.data == "admin:noop")
async def noop(callback: CallbackQuery):
    await callback.answer()
