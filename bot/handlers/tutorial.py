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

DEFAULT_TUTORIALS = {
    "android": (
        "برای اتصال در اندروید می‌توانید از یکی از این نرم‌افزارها استفاده کنید:\n"
        "v2box  یا  NPV Tunnel\n\n"
        "🔹 روش کار با v2box:\n"
        "۱. نرم‌افزار v2box را از گوگل‌پلی نصب کنید.\n"
        "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
        "۳. برنامه را باز کنید و روی علامت + بالای صفحه بزنید.\n"
        "۴. گزینه «Import from Clipboard» (وارد کردن از کلیپ‌بورد) را انتخاب کنید.\n"
        "۵. کانفیگ‌ها اضافه می‌شوند؛ یکی را انتخاب و روی دکمه اتصال بزنید.\n\n"
        "🔹 روش کار با NPV Tunnel:\n"
        "۱. نرم‌افزار NPV Tunnel را نصب کنید.\n"
        "۲. لینک اشتراک را کپی کنید.\n"
        "۳. در برنامه بخش V2ray/Subscription را باز کنید و لینک را وارد (Import) کنید.\n"
        "۴. پس از به‌روزرسانی، یک سرور را انتخاب و متصل شوید."
    ),
    "ios": (
        "برای اتصال در آیفون می‌توانید از یکی از این نرم‌افزارها استفاده کنید:\n"
        "V2rayTun  یا  v2rayBox\n\n"
        "🔹 روش کار با V2rayTun:\n"
        "۱. نرم‌افزار V2rayTun را از App Store نصب کنید.\n"
        "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
        "۳. برنامه را باز کنید و روی علامت + بالای صفحه بزنید.\n"
        "۴. گزینه «Add subscription» یا افزودن از کلیپ‌بورد را انتخاب و لینک را وارد کنید.\n"
        "۵. کانفیگ‌ها اضافه می‌شوند؛ یکی را انتخاب و روی دکمه اتصال بزنید.\n\n"
        "🔹 روش کار با v2rayBox:\n"
        "۱. نرم‌افزار v2rayBox را نصب کنید.\n"
        "۲. لینک اشتراک را کپی کنید.\n"
        "۳. در برنامه روی + بزنید و گزینه Subscription را انتخاب و لینک را وارد کنید.\n"
        "۴. پس از به‌روزرسانی، سرور موردنظر را انتخاب و متصل شوید."
    ),
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
        tutorial_text = DEFAULT_TUTORIALS.get(platform, "")

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
