from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    user_apps_platforms_keyboard, user_apps_list_keyboard,
    admin_apps_platforms_keyboard, admin_apps_list_keyboard, cancel_keyboard
)
from bot.middlewares.membership import check_membership

router = Router()

PLATFORM_NAMES = {"android": "اندروید", "ios": "آیفون", "windows": "ویندوز"}


class AppStates(StatesGroup):
    waiting_url = State()


@router.callback_query(F.data == "main:apps")
async def user_apps(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return
    await callback.message.edit_text(
        "🧩 نرم‌افزارها\n\nپلتفرم خود را انتخاب کنید:",
        reply_markup=user_apps_platforms_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("apps:"))
async def user_apps_list(callback: CallbackQuery):
    platform = callback.data.split(":")[1]
    apps = await db.get_apps(platform)
    name = PLATFORM_NAMES.get(platform, platform)
    if not apps:
        await callback.answer(f"برای {name} فعلا نرم‌افزاری ثبت نشده.", show_alert=True)
        return
    await callback.message.edit_text(
        f"🧩 نرم‌افزارهای {name}:\nروی هر کدام بزنید تا دانلود شود.",
        reply_markup=user_apps_list_keyboard(platform, apps)
    )
    await callback.answer()


@router.callback_query(F.data == "admin:apps")
async def admin_apps(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🧩 مدیریت نرم‌افزارها\n\nپلتفرم را انتخاب کنید:",
        reply_markup=admin_apps_platforms_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:applist:"))
async def admin_apps_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    platform = callback.data.split(":")[2]
    apps = await db.get_apps(platform)
    name = PLATFORM_NAMES.get(platform, platform)
    await callback.message.edit_text(
        f"🧩 نرم‌افزارهای {name} ({len(apps)}):\n"
        "برای حذف روی هر لینک بزنید یا لینک جدید اضافه کنید.",
        reply_markup=admin_apps_list_keyboard(platform, apps)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:app_del:"))
async def admin_app_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    platform = parts[2]
    app_id = int(parts[3])
    await db.delete_app(app_id)

    apps = await db.get_apps(platform)
    name = PLATFORM_NAMES.get(platform, platform)
    await callback.message.edit_text(
        f"🧩 نرم‌افزارهای {name} ({len(apps)}):\n"
        "برای حذف روی هر لینک بزنید یا لینک جدید اضافه کنید.",
        reply_markup=admin_apps_list_keyboard(platform, apps)
    )
    await callback.answer("✅ حذف شد!")


@router.callback_query(F.data.startswith("admin:app_add:"))
async def admin_app_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    platform = callback.data.split(":")[2]
    name = PLATFORM_NAMES.get(platform, platform)
    await state.update_data(app_platform=platform)
    await callback.message.edit_text(
        f"➕ افزودن نرم‌افزار برای {name}\n\n"
        "لینک نرم‌افزار را ارسال کنید:\n(مثال: https://example.com/app.apk)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AppStates.waiting_url)
    await callback.answer()


@router.message(AppStates.waiting_url)
async def admin_app_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    data = await state.get_data()
    platform = data.get("app_platform")
    await db.add_app(platform, url)
    await state.clear()

    apps = await db.get_apps(platform)
    name = PLATFORM_NAMES.get(platform, platform)
    await message.answer(
        f"✅ لینک اضافه شد!\n\n🧩 نرم‌افزارهای {name} ({len(apps)}):",
        reply_markup=admin_apps_list_keyboard(platform, apps)
    )
