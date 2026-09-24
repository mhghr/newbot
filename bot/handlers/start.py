from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from bot.middlewares.membership import check_membership
from bot.keyboards.inline import main_menu_keyboard, join_channel_keyboard, back_to_menu_keyboard
from bot.database import db

router = Router()


SUPPORT_TEXT = (
    "🆘 پشتیبانی\n\n"
    "سلام دوست خوبم 🌸\n\n"
    "به دلیل تغییراتی که اخیراً در بستر اینترنت کشور به‌وجود آمده، ارتباط با آی‌پی‌های "
    "خارج از کشور ناپایدار شده و این موضوع کاملاً خارج از کنترل ماست. بنابراین اگر گاهی "
    "سرویس متصل نمی‌شود یا سرعت پایین می‌آید، متأسفانه از سمت ما کار خاصی برای رفع این "
    "اختلال‌ها برنمی‌آید، چون ریشه‌ی مشکل در زیرساخت اینترنت است.\n\n"
    "برای اینکه تا حد امکان این نقص جبران شود، ما برای هر کاربر چندین لینک با "
    "لوکیشن‌های مختلف و پروتکل‌های متفاوت در نظر گرفته‌ایم؛ پیشنهاد می‌کنیم لینک‌ها و "
    "لوکیشن‌های مختلف را امتحان کنید تا بهترین گزینه را پیدا کنید.\n\n"
    "اگر با این وجود همچنان قطعی یا اختلال داشتید و سرویس به‌کارتان نیامد، می‌توانید از "
    "طریق دکمه‌ی «💵 عودت وجه» درخواست بازگشت وجه را ثبت کنید تا هزینه به شما برگردد.\n\n"
    "ممنون از صبوری و همراهی شما 🙏"
)


def main_menu_text() -> str:
    return (
        "🌸 سلام\n"
        "به ربات میگ‌میگ خوش آمدید 🎉\n\n"
        "🛒 منوی اصلی\n"
        "✅ در حال حاضر دو پروتکل V2Ray و WireGuard ارائه می‌شود.\n\n"
        "از گزینه‌های زیر انتخاب کنید 👇"
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
        main_menu_text(),
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
        main_menu_text(),
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer("✅ عضویت تایید شد!")


@router.callback_query(F.data == "main:home")
async def show_landing(callback: CallbackQuery):
    await callback.message.edit_text(
        main_menu_text(),
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()


@router.callback_query(F.data == "main:configs")
async def show_configs_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        main_menu_text(),
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()


@router.callback_query(F.data == "main:back")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        main_menu_text(),
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()


@router.callback_query(F.data == "main:support")
async def support(callback: CallbackQuery):
    await callback.message.edit_text(
        SUPPORT_TEXT,
        reply_markup=back_to_menu_keyboard()
    )
    await callback.answer()
