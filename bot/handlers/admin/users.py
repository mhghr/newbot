from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import cancel_keyboard, admin_menu_keyboard
from bot.services.xui import XUIClient, format_bytes

router = Router()


class SearchUserStates(StatesGroup):
    waiting_query = State()


@router.callback_query(F.data == "admin:search_user")
async def search_user_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🔍 آیدی عددی تلگرام یا یوزرنیم کاربر را وارد کنید:",
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
        await message.answer(
            "❌ کاربری یافت نشد.",
            reply_markup=admin_menu_keyboard()
        )
        await state.clear()
        return

    if len(users) == 1:
        user = users[0]
        configs = await db.get_user_configs(user["id"])
        orders = await db.get_user_orders(user["id"])

        text = (
            f"👤 اطلاعات کاربر:\n\n"
            f"🆔 آیدی: {user['telegram_id']}\n"
            f"📛 نام: {user['first_name'] or '-'} {user['last_name'] or ''}\n"
            f"👤 یوزرنیم: @{user['username'] or 'ندارد'}\n"
            f"📅 تاریخ عضویت: {user['created_at']}\n\n"
        )

        if configs:
            text += f"📋 کانفیگ‌های فعال ({len(configs)}):\n"
            for c in configs:
                remaining = max(0, (c["expire_date"] - datetime.now()).days)
                text += f"  • {c['server_name']} ({c['location']}) - {remaining} روز مانده\n"
        else:
            text += "📋 کانفیگ فعالی ندارد.\n"

        text += f"\n📦 تعداد سفارشات: {len(orders)}\n"
        if orders:
            for o in orders:
                status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(o["status"], "❓")
                text += f"  {status_emoji} #{o['id']} | {o['plan_name']} | {o['price']:,}T | {o['status']}\n"

        await message.answer(text, reply_markup=admin_menu_keyboard())
    else:
        buttons = []
        for u in users[:10]:
            buttons.append([InlineKeyboardButton(
                text=f"{u['first_name'] or '-'} | @{u['username'] or 'N/A'} | {u['telegram_id']}",
                callback_data=f"admin:user_detail:{u['id']}"
            )])
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
        await message.answer(
            f"🔍 {len(users)} کاربر یافت شد:",
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

    configs = await db.get_user_configs(user["id"])
    orders = await db.get_user_orders(user["id"])

    text = (
        f"👤 اطلاعات کاربر:\n\n"
        f"🆔 آیدی: {user['telegram_id']}\n"
        f"📛 نام: {user['first_name'] or '-'} {user['last_name'] or ''}\n"
        f"👤 یوزرنیم: @{user['username'] or 'ندارد'}\n"
        f"📅 تاریخ عضویت: {user['created_at']}\n\n"
    )

    if configs:
        text += f"📋 کانفیگ‌های فعال ({len(configs)}):\n"
        for c in configs:
            expire_date = datetime.fromisoformat(c["expire_date"])
            remaining = max(0, (expire_date - datetime.now()).days)
            text += f"  • {c['server_name']} ({c['location']}) - {remaining} روز مانده\n"
    else:
        text += "📋 کانفیگ فعالی ندارد.\n"

    text += f"\n📦 تعداد سفارشات: {len(orders)}\n"

    await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    await callback.answer()
