from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    servers_list_keyboard, server_actions_keyboard, server_inbounds_keyboard,
    server_delete_confirm_keyboard, cancel_keyboard
)
from bot.services.xui import XUIClient

router = Router()
logger = logging.getLogger(__name__)

async def _sync_clients_to_inbounds(server: dict, inbound_ids: list, admin_chat_id: int, bot):
    success = 0
    failed = 0

    configs = await db.get_configs_by_server_id(server["id"])
    if not configs:
        await bot.send_message(admin_chat_id, "هیچ کانفیگی برای این سرور یافت نشد.")
        return

    xui = XUIClient(server["url"], server["username"], server["password"], server["api_token"])

    for cfg in configs:
        try:
            ok = await xui.attach_client(cfg["client_email"], inbound_ids)
            if ok:
                success += 1
            else:
                failed += 1
                logger.warning(f"attach_client returned false for {cfg['client_email']}")
        except Exception:
            failed += 1
            logger.exception(f"Sync failed for client {cfg['client_email']}")

    await bot.send_message(
        admin_chat_id,
        f"همگام‌سازی اینباندهای «{server['name']}» کامل شد\n"
        f"موفق: {success}\nخطا: {failed}"
    )

FIELD_LABELS = {
    "name": "نام", "url": "آدرس API", "api_token": "توکن",
    "location": "لوکیشن", "sub_domain": "آدرس ساب", "sub_port": "پورت ساب",
}


class AddServerStates(StatesGroup):
    waiting_url = State()
    waiting_token = State()
    waiting_name = State()
    waiting_location = State()
    waiting_sub_domain = State()
    waiting_sub_port = State()
    waiting_inbounds = State()


class InboundStates(StatesGroup):
    waiting_inbound_ids = State()
    waiting_add_inbound = State()


class ServerEditStates(StatesGroup):
    waiting_value = State()


def _server_header(server) -> str:
    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    return (
        f"🖥 {server['name']} — {server['location']}  ({status})\n"
        "روی هر مورد بزنید تا ویرایش شود:"
    )


# ---------------- Servers list (new: direct list, no sub-menu) ----------------

@router.callback_query(F.data == "admin:servers")
async def servers_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    await state.clear()
    servers = await db.get_all_servers()
    if not servers:
        await callback.message.edit_text(
            "📋 هیچ سروری ثبت نشده است.\nبرای افزودن روی دکمه زیر بزنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ افزودن سرور", callback_data="admin:add_server")],
                [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
            ])
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🖥 مدیریت سرورها\nروی هر سرور بزنید تا ویرایش/تنظیم کنید:",
        reply_markup=servers_list_keyboard(servers)
    )
    await callback.answer()


# ---------------- Add server flow ----------------

@router.callback_query(F.data == "admin:add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🖥 افزودن سرور جدید\n\nآدرس پنل 3x-ui را وارد کنید:\n(مثال: https://panel.example.com:2053/path)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_url)
    await callback.answer()


@router.message(AddServerStates.waiting_url)
async def add_server_url(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.update_data(url=message.text.strip().rstrip("/"))
    await message.answer("🔑 API Token پنل را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_token)


@router.message(AddServerStates.waiting_token)
async def add_server_token(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.update_data(api_token=message.text.strip())
    await message.answer("📛 نام سرور را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_name)


@router.message(AddServerStates.waiting_name)
async def add_server_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.update_data(name=message.text.strip())
    await message.answer("📍 لوکیشن سرور را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_location)


@router.message(AddServerStates.waiting_location)
async def add_server_location(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.update_data(location=message.text.strip())
    await message.answer(
        "🌐 آدرس دامنه یا IP سابسکریپشن را وارد کنید:\n"
        "(برای نمایش لینک ساب به کاربران. خالی بگذارید = آدرس پنل استفاده شود)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_sub_domain)


@router.message(AddServerStates.waiting_sub_domain)
async def add_server_sub_domain(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.update_data(sub_domain=message.text.strip())
    await message.answer(
        "🔢 پورت سابسکریپشن را وارد کنید:\n(پورتی که لینک اشتراک روی آن سرو می‌شود، مثال: 2096)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_sub_port)


@router.message(AddServerStates.waiting_sub_port)
async def add_server_sub_port(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید.", reply_markup=cancel_keyboard())
        return
    await state.update_data(sub_port=int(text))
    data = await state.get_data()
    await message.answer("⏳ در حال تست اتصال به پنل...")
    xui = XUIClient(data["url"], api_token=data["api_token"])
    try:
        inbounds = await xui.get_inbounds()
        inbound_id = inbounds[0].get("id", 0) if inbounds else 0
    except Exception as e:
        await message.answer(f"❌ اتصال به پنل ناموفق بود!\n{str(e)}", reply_markup=cancel_keyboard())
        await state.clear()
        return
    await state.update_data(inbound_id=inbound_id)
    await message.answer(
        "✅ اتصال برقرار شد!\n\n📡 لیست آیدی اینباندهای یوزر را وارد کنید:\n"
        "با فرمت کاما جدا: `10,2,3,7`",
        parse_mode="Markdown", reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_inbounds)


@router.message(AddServerStates.waiting_inbounds)
async def add_server_inbounds(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    ids = db.parse_inbound_ids(message.text)
    if not ids:
        await message.answer("⚠️ آیدی‌های عددی جدا شده با , وارد کنید.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    cleaned = ",".join(str(i) for i in ids)
    server_id = await db.add_server(
        name=data["name"], url=data["url"], location=data["location"],
        api_token=data["api_token"], inbound_id=data.get("inbound_id", 0),
        sub_port=data.get("sub_port", 2096), sub_domain=data.get("sub_domain", ""),
    )
    await db.set_server_inbound_ids(server_id, cleaned)
    await state.clear()

    servers = await db.get_all_servers()
    await message.answer(
        f"✅ سرور «{data['name']}» اضافه شد!", reply_markup=servers_list_keyboard(servers)
    )


# ---------------- Server detail / edit (field buttons) ----------------

@router.callback_query(F.data.startswith("admin:server_detail:"))
async def server_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True); return
    await callback.message.edit_text(
        _server_header(server),
        reply_markup=server_actions_keyboard(server)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_server:"))
async def edit_server_field_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    parts = callback.data.split(":")
    server_id = int(parts[2])
    field = parts[3]
    await state.update_data(server_id=server_id, server_field=field)
    label = FIELD_LABELS.get(field, field)
    hint = ""
    if field == "sub_port":
        hint = "\n(عدد. مثال: 2096)"
    await callback.message.edit_text(
        f"✏️ {label}:\n\nمقدار جدید را وارد کنید:{hint}",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(ServerEditStates.waiting_value)
    await callback.answer()


@router.message(ServerEditStates.waiting_value)
async def edit_server_field_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data()
    server_id = data["server_id"]
    field = data["server_field"]
    value = message.text.strip()
    if field == "sub_port":
        if not value.isdigit():
            await message.answer("⚠️ فقط عدد وارد کنید.", reply_markup=cancel_keyboard()); return
    try:
        await db.update_server_field(server_id, field, value)
    except Exception as e:
        await message.answer(f"❌ خطا: {e}", reply_markup=cancel_keyboard()); await state.clear(); return
    await state.clear()
    server = await db.get_server(server_id)
    await message.answer(
        "✅ به‌روز شد!\n\n" + _server_header(server),
        reply_markup=server_actions_keyboard(server)
    )


# ---------------- Inbounds management ----------------

@router.callback_query(F.data.startswith("admin:inbounds:"))
async def inbounds_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server: await callback.answer("❌", show_alert=True); return
    ids = db.parse_inbound_ids(server["inbound_ids"])
    await callback.message.edit_text(
        f"📡 اینباندهای «{server['name']}»\n\n"
        f"فعلی: {', '.join(str(i) for i in ids) if ids else 'خالی'}\n\n"
        "برای حذف روی هرکدام بزنید:",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:inbound_del:"))
async def inbound_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    parts = callback.data.split(":")
    server_id = int(parts[2]); inbound_id = int(parts[3])
    await db.remove_server_inbound_id(server_id, inbound_id)
    server = await db.get_server(server_id)
    ids = db.parse_inbound_ids(server["inbound_ids"])
    await callback.message.edit_text(
        f"📡 اینباندهای «{server['name']}»\n\n"
        f"فعلی: {', '.join(str(i) for i in ids) if ids else 'خالی'}",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await callback.answer(f"✅ {inbound_id} حذف شد!")
    asyncio.create_task(_sync_clients_to_inbounds(server, ids, callback.from_user.id, callback.bot))


@router.callback_query(F.data.startswith("admin:inbound_add:"))
async def inbound_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    await state.update_data(inbounds_server_id=server_id)
    await callback.message.edit_text("➕ آیدی اینباند را وارد کنید (مثال: 10):", reply_markup=cancel_keyboard())
    await state.set_state(InboundStates.waiting_add_inbound)
    await callback.answer()


@router.message(InboundStates.waiting_add_inbound)
async def inbound_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data(); server_id = data["inbounds_server_id"]
    ids = db.parse_inbound_ids(message.text)
    if not ids: await message.answer("⚠️ ورودی نامعتبر.", reply_markup=cancel_keyboard()); return
    for iid in ids: await db.add_server_inbound_id(server_id, iid)
    await state.clear()
    server = await db.get_server(server_id)
    cur_ids = db.parse_inbound_ids(server["inbound_ids"])
    await message.answer(
        f"✅ اضافه شد!\n📡 فعلی: {', '.join(str(i) for i in cur_ids) if cur_ids else 'خالی'}",
        reply_markup=server_inbounds_keyboard(server_id, cur_ids)
    )
    asyncio.create_task(_sync_clients_to_inbounds(server, cur_ids, message.from_user.id, message.bot))


@router.callback_query(F.data.startswith("admin:set_inbounds:"))
async def set_inbounds_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server: await callback.answer("❌", show_alert=True); return
    await state.update_data(inbounds_server_id=server_id)
    await callback.message.edit_text(
        f"✏️ لیست کامل اینباندها (جایگزین):\nفرمت: 10,2,3,7\n\nفعلی: {server['inbound_ids'] or 'خالی'}",
        parse_mode="Markdown", reply_markup=cancel_keyboard()
    )
    await state.set_state(InboundStates.waiting_inbound_ids)
    await callback.answer()


@router.message(InboundStates.waiting_inbound_ids)
async def save_inbounds(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data(); server_id = data["inbounds_server_id"]
    ids = db.parse_inbound_ids(message.text)
    if not ids: await message.answer("⚠️ نامعتبر.", reply_markup=cancel_keyboard()); return
    cleaned = ",".join(str(i) for i in ids)
    await db.set_server_inbound_ids(server_id, cleaned)
    await state.clear()
    await message.answer(f"✅ ذخیره شد: {cleaned}", reply_markup=server_inbounds_keyboard(server_id, ids))
    server = await db.get_server(server_id)
    asyncio.create_task(_sync_clients_to_inbounds(server, ids, message.from_user.id, message.bot))


# ---------------- Toggle / Delete ----------------

@router.callback_query(F.data.startswith("admin:toggle_server:"))
async def toggle_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    await db.toggle_server(server_id)
    server = await db.get_server(server_id)
    await callback.message.edit_text(
        _server_header(server),
        reply_markup=server_actions_keyboard(server)
    )
    await callback.answer("✅ وضعیت تغییر کرد!")


@router.callback_query(F.data.startswith("admin:delete_server:"))
async def delete_server_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server: await callback.answer("❌", show_alert=True); return
    await callback.message.edit_text(
        f"⚠️ حذف «{server['name']}» ({server['location']})؟\nاین عمل قابل بازگشت نیست.",
        reply_markup=server_delete_confirm_keyboard(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:delete_server_confirm:"))
async def delete_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    await db.delete_server(server_id)
    await callback.answer("✅ حذف شد!")
    servers = await db.get_all_servers()
    await callback.message.edit_text(
        "🖥 مدیریت سرورها", reply_markup=servers_list_keyboard(servers)
    )
