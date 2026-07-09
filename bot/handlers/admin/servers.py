from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    admin_servers_keyboard, server_actions_keyboard, server_inbounds_keyboard,
    server_delete_confirm_keyboard, cancel_keyboard
)
from bot.services.xui import XUIClient

router = Router()


class AddServerStates(StatesGroup):
    waiting_url = State()
    waiting_token = State()
    waiting_name = State()
    waiting_location = State()
    waiting_sub_port = State()
    waiting_inbounds = State()


class InboundStates(StatesGroup):
    waiting_inbound_ids = State()
    waiting_add_inbound = State()


@router.callback_query(F.data == "admin:servers")
async def servers_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.message.edit_text("🖥 مدیریت سرورها:", reply_markup=admin_servers_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🖥 افزودن سرور جدید\n\n"
        "آدرس پنل 3x-ui را وارد کنید:\n"
        "(مثال: https://panel.example.com:2053/path)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_url)
    await callback.answer()


@router.message(AddServerStates.waiting_url)
async def add_server_url(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    url = message.text.strip().rstrip("/")
    await state.update_data(url=url)
    await message.answer("🔑 API Token پنل را وارد کنید:\n(از Settings → Security → API Token)", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_token)


@router.message(AddServerStates.waiting_token)
async def add_server_token(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(api_token=message.text.strip())
    await message.answer("📛 نام سرور را وارد کنید:\n(مثال: سرور اصلی، سرور ۱)", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_name)


@router.message(AddServerStates.waiting_name)
async def add_server_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(name=message.text.strip())
    await message.answer("📍 لوکیشن سرور را وارد کنید:\n(مثال: آلمان، هلند، آمریکا)", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_location)


@router.message(AddServerStates.waiting_location)
async def add_server_location(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    location = message.text.strip()
    await state.update_data(location=location)
    await message.answer(
        "🔢 پورت سابسکریپشن پنل را وارد کنید:\n"
        "(پورت sub که لینک اشتراک روی آن سرو می‌شود، مثال: 2096)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_sub_port)


@router.message(AddServerStates.waiting_sub_port)
async def add_server_sub_port(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید. مثال: 2096", reply_markup=cancel_keyboard())
        return

    await state.update_data(sub_port=int(text))
    data = await state.get_data()
    url = data["url"]
    api_token = data["api_token"]

    await message.answer("⏳ در حال تست اتصال به پنل...")

    xui = XUIClient(url, api_token=api_token)
    try:
        inbounds = await xui.get_inbounds()
        inbound_id = inbounds[0].get("id", 0) if inbounds else 0
    except Exception as e:
        await message.answer(
            f"❌ اتصال به پنل ناموفق بود!\n{str(e)}\n\n"
            "لطفا آدرس و توکن را بررسی کنید.",
            reply_markup=admin_servers_keyboard()
        )
        await state.clear()
        return

    await state.update_data(inbound_id=inbound_id)
    await message.answer(
        "✅ اتصال برقرار شد!\n\n"
        "📡 لیست آیدی اینباندهای یوزر را وارد کنید:\n"
        "این اینباندها موقع ساخت اکانت به کلاینت اتچ می‌شوند.\n"
        "با فرمت زیر و جدا شده با , :\n"
        "`10,2,3,7`",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_inbounds)


@router.message(AddServerStates.waiting_inbounds)
async def add_server_inbounds(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    ids = db.parse_inbound_ids(message.text)
    if not ids:
        await message.answer(
            "⚠️ ورودی نامعتبر است. آیدی‌های عددی جدا شده با , وارد کنید.\n"
            "مثال: 10,2,3,7",
            reply_markup=cancel_keyboard()
        )
        return

    data = await state.get_data()
    cleaned = ",".join(str(i) for i in ids)

    server_id = await db.add_server(
        name=data["name"],
        url=data["url"],
        location=data["location"],
        api_token=data["api_token"],
        inbound_id=data.get("inbound_id", 0),
        sub_port=data.get("sub_port", 2096),
    )
    await db.set_server_inbound_ids(server_id, cleaned)

    await message.answer(
        f"✅ سرور با موفقیت اضافه شد!\n\n"
        f"📛 نام: {data['name']}\n"
        f"📍 لوکیشن: {data['location']}\n"
        f"🔗 آدرس: {data['url']}\n"
        f"🔢 پورت ساب: {data.get('sub_port', 2096)}\n"
        f"📡 اینباندهای یوزر: {cleaned}",
        reply_markup=admin_servers_keyboard()
    )
    await state.clear()


@router.callback_query(F.data == "admin:list_servers")
async def list_servers(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    servers = await db.get_all_servers()
    if not servers:
        await callback.message.edit_text(
            "📋 هیچ سروری ثبت نشده است.",
            reply_markup=admin_servers_keyboard()
        )
        await callback.answer()
        return

    text = "📋 لیست سرورها:\n\n"
    buttons = []
    for s in servers:
        status = "🟢" if s["is_active"] else "🔴"
        text += f"{status} {s['name']} | {s['location']}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {s['name']} - {s['location']}",
            callback_data=f"admin:server_detail:{s['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:server_detail:"))
async def server_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    auth = "🔑 API Token" if server["api_token"] else "👤 Username/Password"
    inbounds_txt = server["inbound_ids"] or "تنظیم نشده"
    await callback.message.edit_text(
        f"🖥 جزئیات سرور:\n\n"
        f"📛 نام: {server['name']}\n"
        f"📍 لوکیشن: {server['location']}\n"
        f"🔗 آدرس: {server['url']}\n"
        f"🔢 پورت ساب: {server['sub_port'] or 2096}\n"
        f"🔐 احراز هویت: {auth}\n"
        f"📡 اینباندهای یوزر: {inbounds_txt}\n"
        f"وضعیت: {status}",
        reply_markup=server_actions_keyboard(server_id, bool(server["is_active"]))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:inbounds:"))
async def inbounds_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    ids = db.parse_inbound_ids(server["inbound_ids"])
    current = ", ".join(str(i) for i in ids) if ids else "خالی"
    await callback.message.edit_text(
        f"📡 مدیریت اینباندهای سرور «{server['name']}»\n\n"
        f"اینباندهای فعلی: {current}\n\n"
        "این اینباندها موقع ساخت اکانت به کلاینت اتچ می‌شوند.\n"
        "برای حذف روی هر اینباند بزنید یا اینباند جدید اضافه کنید:",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:inbound_del:"))
async def inbound_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    server_id = int(parts[2])
    inbound_id = int(parts[3])

    await db.remove_server_inbound_id(server_id, inbound_id)
    server = await db.get_server(server_id)
    ids = db.parse_inbound_ids(server["inbound_ids"])
    current = ", ".join(str(i) for i in ids) if ids else "خالی"

    await callback.message.edit_text(
        f"📡 مدیریت اینباندهای سرور «{server['name']}»\n\n"
        f"اینباندهای فعلی: {current}\n\n"
        "برای حذف روی هر اینباند بزنید یا اینباند جدید اضافه کنید:",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await callback.answer(f"✅ اینباند {inbound_id} حذف شد!")


@router.callback_query(F.data.startswith("admin:inbound_add:"))
async def inbound_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    await state.update_data(inbounds_server_id=server_id)
    await callback.message.edit_text(
        "➕ آیدی اینباند جدید را وارد کنید:\n"
        "(یک عدد، مثال: 10)\n\n"
        "برای افزودن چند اینباند می‌توانید با , جدا کنید. مثال: 10,2,3",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(InboundStates.waiting_add_inbound)
    await callback.answer()


@router.message(InboundStates.waiting_add_inbound)
async def inbound_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    server_id = data.get("inbounds_server_id")

    ids = db.parse_inbound_ids(message.text)
    if not ids:
        await message.answer(
            "⚠️ ورودی نامعتبر است. فقط آیدی عددی مجاز است. مثال: 10",
            reply_markup=cancel_keyboard()
        )
        return

    for iid in ids:
        await db.add_server_inbound_id(server_id, iid)

    server = await db.get_server(server_id)
    cur_ids = db.parse_inbound_ids(server["inbound_ids"])
    current = ", ".join(str(i) for i in cur_ids) if cur_ids else "خالی"

    await message.answer(
        f"✅ اضافه شد!\n📡 اینباندهای فعلی: {current}",
        reply_markup=server_inbounds_keyboard(server_id, cur_ids)
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:set_inbounds:"))
async def set_inbounds_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    current = server["inbound_ids"] or "تنظیم نشده"
    await state.update_data(inbounds_server_id=server_id)
    await callback.message.edit_text(
        "✏️ لیست کامل اینباندها را وارد کنید (جایگزین لیست فعلی می‌شود):\n\n"
        "آیدی‌ها را با , از هم جدا کنید.\n"
        "مثال: `10,2,3,7`\n\n"
        f"مقدار فعلی: {current}",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(InboundStates.waiting_inbound_ids)
    await callback.answer()


@router.message(InboundStates.waiting_inbound_ids)
async def save_inbounds(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    server_id = data.get("inbounds_server_id")

    ids = db.parse_inbound_ids(message.text)
    if not ids:
        await message.answer(
            "⚠️ ورودی نامعتبر است. فقط آیدی‌های عددی جدا شده با , مجاز است.\n"
            "مثال: 10,2,3,7",
            reply_markup=cancel_keyboard()
        )
        return

    cleaned = ",".join(str(i) for i in ids)
    await db.set_server_inbound_ids(server_id, cleaned)

    await message.answer(
        f"✅ اینباندهای یوزر ذخیره شد:\n📡 {cleaned}",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:toggle_server:"))
async def toggle_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    await db.toggle_server(server_id)
    await callback.answer("✅ وضعیت سرور تغییر کرد!")

    server = await db.get_server(server_id)
    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    auth = "🔑 API Token" if server["api_token"] else "👤 Username/Password"
    inbounds_txt = server["inbound_ids"] or "تنظیم نشده"
    await callback.message.edit_text(
        f"🖥 جزئیات سرور:\n\n"
        f"📛 نام: {server['name']}\n"
        f"📍 لوکیشن: {server['location']}\n"
        f"🔗 آدرس: {server['url']}\n"
        f"🔢 پورت ساب: {server['sub_port'] or 2096}\n"
        f"🔐 احراز هویت: {auth}\n"
        f"📡 اینباندهای یوزر: {inbounds_txt}\n"
        f"وضعیت: {status}",
        reply_markup=server_actions_keyboard(server_id, bool(server["is_active"]))
    )


@router.callback_query(F.data.startswith("admin:delete_server:"))
async def delete_server_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    await callback.message.edit_text(
        f"⚠️ آیا از حذف این سرور مطمئن هستید؟\n\n"
        f"📛 نام: {server['name']}\n"
        f"📍 لوکیشن: {server['location']}\n"
        f"🔗 آدرس: {server['url']}\n\n"
        "این عمل قابل بازگشت نیست.",
        reply_markup=server_delete_confirm_keyboard(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:delete_server_confirm:"))
async def delete_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    await db.delete_server(server_id)
    await callback.answer("✅ سرور حذف شد!")
    await callback.message.edit_text("🖥 مدیریت سرورها:", reply_markup=admin_servers_keyboard())
