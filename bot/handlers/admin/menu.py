from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot.config import ADMIN_IDS
from bot.keyboards.inline import admin_menu_keyboard

router = Router()


@router.callback_query(F.data == "main:admin")
async def admin_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    await callback.message.edit_text("⚙️ پنل مدیریت:", reply_markup=admin_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    await callback.message.edit_text("⚙️ پنل مدیریت:", reply_markup=admin_menu_keyboard())
    await callback.answer()
