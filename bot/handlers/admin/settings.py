from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    admin_settings_keyboard, admin_tutorial_keyboard,
    admin_menu_keyboard, cancel_keyboard,
    proxy_menu_keyboard, proxy_sources_keyboard,
    proxy_delete_confirm_keyboard,
    admin_tutorial_apps_keyboard, admin_tutorial_app_keyboard,
)
from bot.handlers.tutorial import DEFAULT_APP_TUTORIALS
from bot.services.proxy_scanner import test_scan

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
    waiting_tutorial_video = State()
    waiting_card_full_number = State()
    waiting_card_full_holder = State()


class ProxyStates(StatesGroup):
    waiting_source_channel = State()
    waiting_target_channel = State()


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
async def edit_tutorial(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    platform = callback.data.split(":")[2]
    platform_name = PLATFORM_NAMES.get(platform, platform)
    apps = DEFAULT_APP_TUTORIALS.get(platform, {})
    app_list = [{"slug": slug, "name": app["name"]} for slug, app in apps.items()]
    if not app_list:
        await callback.answer(f"برای {platform_name} نرم‌افزاری تعریف نشده.", show_alert=True)
        return
    await callback.message.edit_text(
        f"📖 مدیریت آموزش {platform_name}:\nروی هر نرم‌افزار بزنید تا ویرایش شود:",
        reply_markup=admin_tutorial_apps_keyboard(platform, app_list)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:tut_app:"))
async def admin_tutorial_app_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    platform, slug = parts[2], parts[3]
    app = DEFAULT_APP_TUTORIALS.get(platform, {}).get(slug)
    if not app:
        await callback.answer("❌ یافت نشد!", show_alert=True)
        return

    text = await db.get_setting(f"tut_text_{platform}_{slug}", "")
    if not text:
        text = app["text"]
    video = await db.get_setting(f"tut_video_{platform}_{slug}", "")
    video_status = "✅ تنظیم شده" if video else "❌ تنظیم نشده"
    snippet = text if len(text) <= 300 else text[:297] + "…"

    await callback.message.edit_text(
        f"📖 آموزش {app['name']}:\n\n"
        f"متن فعلی:\n{snippet}\n\n"
        f"🎬 ویدیو آموزشی: {video_status}",
        reply_markup=admin_tutorial_app_keyboard(platform, slug, has_video=bool(video))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:tut_text:"))
async def admin_tutorial_text_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    platform, slug = parts[2], parts[3]
    await state.update_data(tut_platform=platform, tut_slug=slug)
    await callback.message.edit_text(
        "📝 متن آموزش جدید را ارسال کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_tutorial_text)
    await callback.answer()


@router.message(SettingsStates.waiting_tutorial_text)
async def save_tutorial(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    platform = data.get("tut_platform")
    slug = data.get("tut_slug")
    await db.set_setting(f"tut_text_{platform}_{slug}", message.text)
    await state.clear()

    app = DEFAULT_APP_TUTORIALS.get(platform, {}).get(slug, {})
    video = await db.get_setting(f"tut_video_{platform}_{slug}", "")
    video_status = "✅ تنظیم شده" if video else "❌ تنظیم نشده"
    text = message.text if len(message.text) <= 300 else message.text[:297] + "…"
    await message.answer(
        f"✅ متن آموزش {app.get('name', slug)} ذخیره شد!\n\n"
        f"متن فعلی:\n{text}\n\n"
        f"🎬 ویدیو آموزشی: {video_status}",
        reply_markup=admin_tutorial_app_keyboard(platform, slug, has_video=bool(video))
    )


@router.callback_query(F.data.startswith("admin:tut_video:"))
async def admin_tutorial_video_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    platform, slug = parts[2], parts[3]
    await state.update_data(tut_platform=platform, tut_slug=slug)
    await callback.message.edit_text(
        "🎬 ویدیو آموزشی را ارسال کنید.\n"
        "(اختیاری است — برای لغو دکمه «انصراف» را بزنید)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_tutorial_video)
    await callback.answer()


@router.message(SettingsStates.waiting_tutorial_video)
async def save_tutorial_video(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.video:
        await message.answer(
            "🎬 یک ویدیو ارسال کنید، یا برای لغو دکمه «انصراف» را بزنید:",
            reply_markup=cancel_keyboard()
        )
        return

    data = await state.get_data()
    platform = data.get("tut_platform")
    slug = data.get("tut_slug")
    await db.set_setting(f"tut_video_{platform}_{slug}", message.video.file_id)
    await state.clear()

    app = DEFAULT_APP_TUTORIALS.get(platform, {}).get(slug, {})
    await message.answer(
        f"✅ ویدیو آموزشی {app.get('name', slug)} ذخیره شد!",
        reply_markup=admin_tutorial_app_keyboard(platform, slug, has_video=True)
    )


@router.callback_query(F.data.startswith("admin:tut_video_del:"))
async def admin_tutorial_video_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    platform, slug = parts[2], parts[3]
    await db.set_setting(f"tut_video_{platform}_{slug}", "")

    app = DEFAULT_APP_TUTORIALS.get(platform, {}).get(slug, {})
    text = await db.get_setting(f"tut_text_{platform}_{slug}", "")
    if not text:
        text = app.get("text", "")
    snippet = text if len(text) <= 300 else text[:297] + "…"
    await callback.message.edit_text(
        f"🗑 ویدیو حذف شد!\n\n📖 آموزش {app.get('name', slug)}:\n\nمتن فعلی:\n{snippet}\n\n🎬 ویدیو آموزشی: ❌ تنظیم نشده",
        reply_markup=admin_tutorial_app_keyboard(platform, slug, has_video=False)
    )
    await callback.answer()


# ---- Proxy menu ----

@router.callback_query(F.data == "admin:proxy")
async def proxy_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    sources = await db.get_all_proxy_sources()
    target = await db.get_proxy_target()
    await callback.message.edit_text(
        f"🔄 مدیریت پروکسی\n\n"
        f"🎯 کانال مقصد: {target or '(تنظیم نشده)'}\n"
        f"📡 تعداد کانال‌های منبع: {len(sources)}\n\n"
        "برای حذف روی کانال بزنید:",
        reply_markup=proxy_sources_keyboard(sources)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:proxy_askdel:"))
async def proxy_ask_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    source_id = int(callback.data.split(":")[2])
    sources = await db.get_all_proxy_sources()
    src = next((s for s in sources if s["id"] == source_id), None)
    if not src:
        await callback.answer("❌ کانال یافت نشد!", show_alert=True)
        return
    await callback.message.edit_text(
        f"⚠️ آیا از حذف کانال `{src['channel']}` اطمینان دارید؟",
        parse_mode="Markdown",
        reply_markup=proxy_delete_confirm_keyboard(source_id)
    )
    await callback.answer()


@router.callback_query(F.data == "admin:proxy_add")
async def proxy_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "➕ کانال منبع پروکسی را وارد کنید (یوزرنیم مثل @proxy_channel):",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(ProxyStates.waiting_source_channel)
    await callback.answer()


@router.message(ProxyStates.waiting_source_channel)
async def proxy_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await db.add_proxy_source(message.text.strip())
    await state.clear()
    sources = await db.get_all_proxy_sources()
    target = await db.get_proxy_target()
    await message.answer(
        f"✅ کانال منبع اضافه شد!\n\n"
        f"🎯 کانال مقصد: {target or '(تنظیم نشده)'}\n"
        f"📡 کانال‌های منبع: {len(sources)} عدد",
        reply_markup=proxy_sources_keyboard(sources)
    )


@router.callback_query(F.data == "admin:proxy_list")
async def proxy_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    sources = await db.get_all_proxy_sources()
    target = await db.get_proxy_target()
    await callback.message.edit_text(
        f"📋 کانال‌های منبع:\n\n"
        f"🎯 کانال مقصد: {target or '(تنظیم نشده)'}\n"
        f"📡 کانال‌ها: {len(sources)} عدد",
        reply_markup=proxy_sources_keyboard(sources)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:proxy_del:"))
async def proxy_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    sid = int(callback.data.split(":")[2])
    await db.delete_proxy_source(sid)
    await callback.answer("✅ حذف شد!")
    sources = await db.get_all_proxy_sources()
    target = await db.get_proxy_target()
    await callback.message.edit_text(
        f"✅ کانال حذف شد!\n\n"
        f"🎯 کانال مقصد: {target or '(تنظیم نشده)'}\n"
        f"📡 کانال‌های منبع: {len(sources)} عدد",
        reply_markup=proxy_sources_keyboard(sources)
    )


@router.callback_query(F.data == "admin:proxy_test")
async def proxy_test(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.answer("🧪 در حال تست...")
    await callback.message.edit_text(
        "🧪 تست ارسال پروکسی شروع شد...\nنتیجه به صورت پیام برای شما ارسال می‌شود.",
        reply_markup=proxy_sources_keyboard(await db.get_all_proxy_sources())
    )
    await test_scan(bot, callback.from_user.id)


@router.callback_query(F.data == "admin:proxy_target")
async def proxy_target_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    current = await db.get_proxy_target()
    await callback.message.edit_text(
        f"🎯 کانال مقصد پروکسی را وارد کنید:\nفعلی: {current or '(تنظیم نشده)'}",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(ProxyStates.waiting_target_channel)
    await callback.answer()


@router.message(ProxyStates.waiting_target_channel)
async def proxy_target_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    target = message.text.strip()
    await db.set_setting("proxy_target_channel", target)
    await state.clear()
    sources = await db.get_all_proxy_sources()
    await message.answer(
        f"✅ کانال مقصد ذخیره شد: {target}\n\n📡 کانال‌های منبع: {len(sources)} عدد",
        reply_markup=proxy_sources_keyboard(sources)
    )
