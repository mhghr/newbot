from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

from bot.database import db
from bot.keyboards.inline import (
    tutorial_platforms_keyboard, tutorial_apps_keyboard, back_to_menu_keyboard,
)
from bot.middlewares.membership import check_membership

router = Router()

PLATFORM_NAMES = {
    "android": "اندروید",
    "ios": "iOS",
    "windows": "ویندوز",
}

DEFAULT_APP_TUTORIALS = {
    "android": {
        "v2box": {
            "name": "v2box",
            "text": (
                "۱. نرم‌افزار v2box را از گوگل‌پلی نصب کنید.\n"
                "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
                "۳. برنامه را باز کنید و روی علامت + بالای صفحه بزنید.\n"
                "۴. گزینه «Import from Clipboard» را انتخاب کنید.\n"
                "۵. کانفیگ‌ها اضافه می‌شوند؛ یکی را انتخاب و روی دکمه اتصال بزنید."
            ),
        },
        "npv_tunnel": {
            "name": "NPV Tunnel",
            "text": (
                "۱. نرم‌افزار NPV Tunnel را نصب کنید.\n"
                "۲. لینک اشتراک را کپی کنید.\n"
                "۳. در برنامه بخش V2Ray/Subscription را باز کنید و لینک را وارد (Import) کنید.\n"
                "۴. پس از به‌روزرسانی، یک سرور را انتخاب و متصل شوید."
            ),
        },
        "v2app": {
            "name": "V2App",
            "text": (
                "۱. نرم‌افزار V2App را از گوگل‌پلی نصب کنید.\n"
                "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
                "۳. برنامه را باز کنید، از نوار پایین روی تب Subscriptions بزنید.\n"
                "۴. روی علامت + پایین صفحه بزنید و گزینه Import from Clipboard را انتخاب کنید.\n"
                "۵. کانفیگ‌ها اضافه می‌شوند؛ یکی را انتخاب کنید و روی دکمه Connect بزنید.\n"
                "۶. در صورت درخواست مجوز VPN، گزینه Allow را انتخاب کنید."
            ),
        },
        "netmod": {
            "name": "NetMod",
            "text": (
                "۱. نرم‌افزار NetMod را از گوگل‌پلی نصب کنید.\n"
                "۲. لینک اشتراک را از بخش «کانفیگ‌های من» کپی کنید.\n"
                "۳. برنامه را باز کنید، از نوار پایین روی تب Configs بزنید.\n"
                "۴. روی علامت + بزنید و گزینه Import Subscription را انتخاب کنید.\n"
                "۵. لینک اشتراک را در کادر URL جای‌گذاری کرده و روی Import بزنید.\n"
                "۶. پس از به‌روزرسانی، کانفیگ موردنظر را انتخاب و روی دکمه Start بزنید."
            ),
        },
        "v2rayng": {
            "name": "v2rayNG",
            "text": (
                "۱. نرم‌افزار v2rayNG را از گوگل‌پلی نصب کنید.\n"
                "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
                "۳. برنامه را باز کنید و روی علامت + بالای صفحه بزنید.\n"
                "۴. گزینه «Import config from Clipboard» را انتخاب کنید.\n"
                "۵. کانفیگ‌ها اضافه می‌شوند؛ یکی را انتخاب و روی دکمه اتصال بزنید."
            ),
        },
    },
    "ios": {
        "v2raytun": {
            "name": "V2rayTun",
            "text": (
                "۱. نرم‌افزار V2rayTun را از App Store نصب کنید.\n"
                "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
                "۳. برنامه را باز کنید و روی علامت + بالای صفحه بزنید.\n"
                "۴. گزینه «Add subscription» یا افزودن از کلیپ‌بورد را انتخاب و لینک را وارد کنید.\n"
                "۵. کانفیگ‌ها اضافه می‌شوند؛ یکی را انتخاب و روی دکمه اتصال بزنید."
            ),
        },
        "v2raybox": {
            "name": "v2rayBox",
            "text": (
                "۱. نرم‌افزار v2rayBox را نصب کنید.\n"
                "۲. لینک اشتراک را کپی کنید.\n"
                "۳. در برنامه روی + بزنید و گزینه Subscription را انتخاب و لینک را وارد کنید.\n"
                "۴. پس از به‌روزرسانی، سرور موردنظر را انتخاب و متصل شوید."
            ),
        },
    },
    "windows": {
        "v2rayn": {
            "name": "v2rayN",
            "text": (
                "۱. نرم‌افزار v2rayN را دانلود کنید، از حالت فشرده خارج کرده و فایل v2rayN.exe را اجرا کنید.\n"
                "۲. لینک اشتراک خود را از بخش «کانفیگ‌های من» کپی کنید.\n"
                "۳. از نوار بالای برنامه وارد منوی «Subscription» (اشتراک) شوید و گزینه "
                "«Subscription group settings» را باز کنید.\n"
                "۴. یک ردیف جدید بسازید، لینک اشتراک را در ستون URL جای‌گذاری کنید و ذخیره کنید (OK).\n"
                "۵. دوباره از منوی «Subscription» گزینه «Update subscription without proxy» را بزنید "
                "تا کانفیگ‌ها اضافه شوند.\n"
                "۶. از لیست، یک سرور را انتخاب کنید (دابل‌کلیک تا فعال شود).\n"
                "۷. در پایین سمت راست برنامه، حالت سیستم را روی «Auto system proxy» بگذارید.\n"
                "۸. حالا متصل هستید. برای قطع، حالت را روی «Clear system proxy» بگذارید."
            ),
        },
    },
}


def _platform_apps(platform: str) -> list:
    apps = DEFAULT_APP_TUTORIALS.get(platform, {})
    return [{"slug": slug, "name": app["name"]} for slug, app in apps.items()]


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
async def tutorial_apps(callback: CallbackQuery):
    platform = callback.data.split(":")[1]
    platform_name = PLATFORM_NAMES.get(platform, platform)
    apps = _platform_apps(platform)
    if not apps:
        await callback.answer(f"برای {platform_name} نرم‌افزاری تعریف نشده.", show_alert=True)
        return

    await callback.message.edit_text(
        f"📖 آموزش اتصال — {platform_name}:\nنرم‌افزار مورد نظر خود را انتخاب کنید:",
        reply_markup=tutorial_apps_keyboard(platform, apps)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tut_app:"))
async def show_app_tutorial(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    platform = parts[1]
    slug = parts[2]

    app = DEFAULT_APP_TUTORIALS.get(platform, {}).get(slug)
    if not app:
        await callback.answer("❌ نرم‌افزار یافت نشد!", show_alert=True)
        return

    text = await db.get_setting(f"tut_text_{platform}_{slug}", "")
    if not text:
        text = app["text"]

    video = await db.get_setting(f"tut_video_{platform}_{slug}", "")
    platform_name = PLATFORM_NAMES.get(platform, platform)
    caption = f"📖 آموزش {app['name']} ({platform_name}):\n\n{text}"

    if video:
        await bot.send_video(
            chat_id=callback.from_user.id,
            video=video,
            caption=caption,
            reply_markup=back_to_menu_keyboard(),
        )
    else:
        await callback.message.edit_text(
            caption,
            disable_web_page_preview=True,
            reply_markup=back_to_menu_keyboard()
        )
    await callback.answer()
