from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from bot.middlewares.membership import check_membership
from bot.keyboards.inline import main_menu_keyboard, join_channel_keyboard
from bot.database import db

router = Router()


def welcome_text() -> str:
    return (
        "🌸 سلام\n"
        "به ربات **میگ‌میگ** خوش آمدید 🎉\n\n"
        "این ربات برای کمک به اتصال شما به اینترنت آزاد، پرسرعت و بدون محدودیت طراحی شده است.\n\n"
        "برای شروع، از منوی زیر گزینه‌ی موردنظر را انتخاب کنید 👇"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    is_member = await check_membership(bot, message.from_user.id)

    if not is_member:
        await message.answer(
            "⚠️ شما عضو کانال ما نیستید!\n\n"
            "لطفا از طریق دکمه زیر در کانال ما عضو شوید و سپس دوباره /start بزنید.",
            reply_markup=join_channel_keyboard()
        )
        return

    await db.add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    await message.answer(
        welcome_text(),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(message.from_user.id)
    )


@router.callback_query(F.data == "check_membership")
async def recheck_membership(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer(
            "⚠️ هنوز عضویت شما تایید نشد. لطفا مطمئن شوید در کانال عضو شده‌اید و دوباره تلاش کنید.",
            show_alert=True
        )
        return

    await db.add_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
    )

    await callback.message.edit_text(
        welcome_text(),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer("✅ عضویت تایید شد!")


@router.callback_query(F.data == "main:back")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        welcome_text(),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()
