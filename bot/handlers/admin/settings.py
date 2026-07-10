from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    admin_settings_keyboard, admin_tutorial_keyboard,
    admin_menu_keyboard, cancel_keyboard
)
from bot.handlers.tutorial import DEFAULT_TUTORIALS

router = Router()

PLATFORM_NAMES = {
    "android": "اندروید",
    "ios": "iOS",
    "windows": "ویندوز",
}


class SettingsStates(StatesGroup):
    waiting_card_number = State()
    waiting_card_holder = State()
    waiting_tutorial_text = State()
    waiting_card_full_number = State()
    waiting_card_full_holder = State()


@router.callback_query(F.data == "admin:card")
async def card_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    card_number = await db.get_setting("card_number", "تنظیم نشده")
    card_holder = await db.get_setting("card_holder", "تنظیم نشده")

    await callback.message.edit_text(
        "💳 تنظیم شماره کارت\n\n"
        f"شماره فعلی: {card_number}\n"
        f"صاحب حساب فعلی: {card_holder}\n\n"
        "شماره کارت جدید را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_card_full_number)
    await callback.answer()


@router.message(SettingsStates.waiting_card_full_number)
async def card_number_step(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(card_number=message.text.strip())
    await message.answer(
        "👤 اکنون نام صاحب حساب را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_card_full_holder)


@router.message(SettingsStates.waiting_card_full_holder)
async def card_holder_step(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    card_number = data.get("card_number", "")
    card_holder = message.text.strip()

    await db.set_setting("card_number", card_number)
    await db.set_setting("card_holder", card_holder)

    await message.answer(
        "✅ اطلاعات کارت ذخیره شد!\n\n"
        f"💳 شماره کارت: {card_number}\n"
        f"👤 صاحب حساب: {card_holder}",
        reply_markup=admin_menu_keyboard()
    )
    await state.clear()


@router.callback_query(F.data == "admin:settings")
async def settings_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    card_number = await db.get_setting("card_number", "تنظیم نشده")
    card_holder = await db.get_setting("card_holder", "تنظیم نشده")

    await callback.message.edit_text(
        f"⚙️ تنظیمات:\n\n"
        f"💳 شماره کارت: {card_number}\n"
        f"👤 صاحب کارت: {card_holder}\n",
        reply_markup=admin_settings_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin:set_card_number")
async def set_card_number(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "💳 شماره کارت جدید را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_card_number)
    await callback.answer()


@router.message(SettingsStates.waiting_card_number)
async def save_card_number(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await db.set_setting("card_number", message.text.strip())
    await message.answer("✅ شماره کارت ذخیره شد!", reply_markup=admin_settings_keyboard())
    await state.clear()


@router.callback_query(F.data == "admin:set_card_holder")
async def set_card_holder(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "👤 نام صاحب کارت را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_card_holder)
    await callback.answer()


@router.message(SettingsStates.waiting_card_holder)
async def save_card_holder(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await db.set_setting("card_holder", message.text.strip())
    await message.answer("✅ نام صاحب کارت ذخیره شد!", reply_markup=admin_settings_keyboard())
    await state.clear()


@router.callback_query(F.data == "admin:tutorials")
async def tutorials_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "📖 مدیریت آموزش‌ها:\nپلتفرم مورد نظر را انتخاب کنید:",
        reply_markup=admin_tutorial_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_tutorial:"))
async def edit_tutorial(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    platform = callback.data.split(":")[2]
    platform_name = PLATFORM_NAMES.get(platform, platform)

    current_text = await db.get_setting(f"tutorial_{platform}", "")
    if not current_text:
        current_text = DEFAULT_TUTORIALS.get(platform, "تنظیم نشده")

    await state.update_data(tutorial_platform=platform)
    await callback.message.edit_text(
        f"📖 آموزش {platform_name}:\n\n"
        f"متن فعلی:\n{current_text}\n\n"
        "متن جدید را ارسال کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_tutorial_text)
    await callback.answer()


@router.message(SettingsStates.waiting_tutorial_text)
async def save_tutorial(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    platform = data.get("tutorial_platform")
    await db.set_setting(f"tutorial_{platform}", message.text)
    platform_name = PLATFORM_NAMES.get(platform, platform)
    await message.answer(
        f"✅ آموزش {platform_name} ذخیره شد!",
        reply_markup=admin_tutorial_keyboard()
    )
    await state.clear()
