from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

from bot.database import db
from bot.keyboards.inline import tutorial_platforms_keyboard, back_to_menu_keyboard
from bot.middlewares.membership import check_membership

router = Router()

PLATFORM_NAMES = {
    "android": "اندروید",
    "ios": "iOS",
    "windows": "ویندوز",
}


@router.callback_query(F.data == "main:tutorial")
async def tutorial_menu(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return

    await callback.message.edit_text(
        "📖 پلتفرم مورد نظر خود را انتخاب کنید:",
        reply_markup=tutorial_platforms_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tutorial:"))
async def show_tutorial(callback: CallbackQuery):
    platform = callback.data.split(":")[1]
    platform_name = PLATFORM_NAMES.get(platform, platform)

    tutorial_text = await db.get_setting(f"tutorial_{platform}", "")

    if not tutorial_text:
        await callback.message.edit_text(
            f"📖 آموزش {platform_name}:\n\n"
            "⚠️ متن آموزش هنوز توسط ادمین تنظیم نشده است.",
            reply_markup=back_to_menu_keyboard()
        )
    else:
        await callback.message.edit_text(
            f"📖 آموزش {platform_name}:\n\n{tutorial_text}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=back_to_menu_keyboard()
        )
    await callback.answer()
