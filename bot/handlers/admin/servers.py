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
    server_delete_confirm_keyboard, cancel_keyboard,
)
from bot.services.xui import XUIClient
from bot.services import panel_sync

router = Router()
logger = logging.getLogger(__name__)

async def _sync_clients_to_inbounds(server: dict, admin_chat_id: int, bot, status_message=None):
    try:
        xui = XUIClient(server["url"], server["username"], server["password"], server["api_token"])

        # Only sync against inbounds that actually exist on the panel. A stale
        # id left in the server settings used to make every account fail its
        # re-attach; now it is simply ignored (and logged by the client).
        desired_ids = await xui.resolve_inbound_ids(db.parse_inbound_ids(server["inbound_ids"]))

        if not desired_ids:
            msg = f"همگام‌سازی «{server['name']}» لغو شد — هیچ اینباند معتبری در تنظیمات نیست."
            if status_message:
                await status_message.edit_text(msg)
            else:
                await bot.send_message(admin_chat_id, msg)
            return

        configs = await db.get_configs_by_server_id(server["id"])
        if not configs:
            msg = "هیچ کانفیگ فعالی برای این سرور یافت نشد."
            if status_message:
                await status_message.edit_text(msg)
            else:
                await bot.send_message(admin_chat_id, msg)
            return

        if status_message:
            await status_message.edit_text(f"⏳ در حال بررسی {len(configs)} کانفیگ...")

        fixed = 0
        already_ok = 0
        failed = 0
        desired_set = set(desired_ids)

        for cfg in configs:
            try:
                current = await xui.get_client_inbounds(cfg["client_email"])
                current_set = set(current)
                if current_set == desired_set:
                    already_ok += 1
                else:
                    # Apply only the delta. Attach first so a client is never
                    # left temporarily without an inbound during the sync.
                    to_add = sorted(desired_set - current_set)
                    to_remove = sorted(current_set - desired_set)
                    attached = not to_add or await xui.attach_client(cfg["client_email"], to_add)
                    # Removal is independent from attachment: an attach error
                    # must not leave obsolete inbounds connected to the client.
                    detached = not to_remove or await xui.detach_client(cfg["client_email"], to_remove)

                    # Never report success only because the API returned 200;
                    # read the panel again and verify the final attachments.
                    actual = set(await xui.get_client_inbounds(cfg["client_email"]))
                    if attached and detached and actual == desired_set:
                        fixed += 1
                    else:
                        failed += 1
                        logger.error(
                            "Inbound sync verification failed for %s: desired=%s actual=%s add=%s remove=%s",
                            cfg["client_email"], sorted(desired_set), sorted(actual), to_add, to_remove,
                        )
            except Exception:
                failed += 1
                logger.exception(f"Sync failed for client {cfg['client_email']}")

        msg = (
            f"گزارش همگام‌سازی «{server['name']}»\n\n"
            f"از قبل درست: {already_ok}\n"
            f"اصلاح شد: {fixed}\n"
            f"خطا: {failed}\n"
            f"مجموع: {len(configs)}"
        )
        if status_message:
            await status_message.edit_text(msg)
        else:
            await bot.send_message(admin_chat_id, msg)
    except Exception:
        logger.exception("_sync_clients_to_inbounds crashed")
        try:
            await bot.send_message(
                admin_chat_id,
                f"خطا در همگام‌سازی «{server['name']}»\nلطفا لاگ‌ها را بررسی کنید."
            )
        except Exception:
            logger.exception("Failed to send sync error notification to admin")

FIELD_LABELS = {
    "name": "نام", "url": "آدرس", "api_token": "توکن",
    "location": "لوکیشن", "sub_domain": "آدرس ساب", "sub_port": "پورت ساب",
    "username": "یوزر SSH/API", "password": "پسورد SSH/API", "api_port": "پورت SSH/API",
}


class AddServerStates(StatesGroup):
    # V2Ray (3x-ui)
    waiting_url = State()
    waiting_token = State()
    waiting_name = State()
    waiting_location = State()
    waiting_inbounds = State()


class InboundStates(StatesGroup):
    waiting_inbound_ids = State()
    waiting_add_inbound = State()


class ServerEditStates(StatesGroup):
    waiting_value = State()


def _server_header(server, role: str = "") -> str:
    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    lines = [
        f"🖥 {server['name']} — {server['location']}  ({status})",
    ]
    if role:
        lines.append(f"📍 وضعیت: {role}")
    lines.append("روی هر مورد بزنید تا ویرایش شود:")
    return "\n".join(lines)


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
        "🖥 مدیریت سرورها\n\n"
        "روی هر سرور بزنید تا ویرایش/تنظیم کنید:",
        reply_markup=servers_list_keyboard(servers)
    )
    await callback.answer()


# ---------------- Add server flow ----------------

@router.callback_query(F.data == "admin:add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "🖥 افزودن سرور V2Ray\n\nآدرس پنل 3x-ui را وارد کنید:\n(مثال: https://panel.example.com:2053/path)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_url)
    await callback.answer()


@router.callback_query(F.data == "new_server_type:v2ray")
async def add_server_v2ray_compat(callback: CallbackQuery, state: FSMContext):
    """Compatibility for keyboards sent before WireGuard was removed."""
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "🖥 افزودن سرور V2Ray\n\nآدرس پنل 3x-ui را وارد کنید:\n(مثال: https://panel.example.com:2053/path)",
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
    if field in ("sub_port", "api_port"):
        hint = "\n(عدد)"
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
    if field in ("sub_port", "api_port"):
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


# ---------------- Sync Inbounds ----------------

@router.callback_query(F.data.startswith("admin:sync_inbounds:"))
async def sync_inbounds(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد", show_alert=True)
        return

    status = await callback.message.answer("⏳ شروع همگام‌سازی...")
    await callback.answer()
    asyncio.create_task(_sync_clients_to_inbounds(server, callback.from_user.id, callback.bot, status_message=status))


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


@router.callback_query(F.data.startswith("admin:toggle_server_sub:"))
async def toggle_server_sub(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    await db.toggle_server_sub(server_id)
    server = await db.get_server(server_id)
    await callback.message.edit_text(
        _server_header(server),
        reply_markup=server_actions_keyboard(server)
    )
    await callback.answer("✅ وضعیت لینک ساب تغییر کرد!")


@router.callback_query(F.data.startswith("admin:toggle_server_wireguard:"))
async def toggle_server_wireguard(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    await db.toggle_server_wireguard(server_id)
    server = await db.get_server(server_id)
    await callback.message.edit_text(
        _server_header(server),
        reply_markup=server_actions_keyboard(server)
    )
    await callback.answer("✅ وضعیت وایرگارد تغییر کرد!")


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


# ---------------- Active server (Arvan DNS + sync) ----------------

@router.callback_query(F.data == "admin:active_server")
async def active_server_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nodes = await db.get_iran_nodes()
    if not nodes:
        await callback.answer("❌ هیچ سرور ایرانی ثبت نشده (لوکیشن iran).", show_alert=True)
        return
    active_id = await db.get_setting("active_node_id", "")
    rows = []
    for n in nodes:
        mark = "✅ " if str(n["id"]) == str(active_id) else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{n['name']} — {n['ip']}", callback_data=f"admin:set_active:{n['id']}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")])
    await callback.message.edit_text(
        "🟢 انتخاب سرور فعال (دامنه به این سرور اشاره می‌کند):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:set_active:"))
async def set_active_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    node_id = int(callback.data.split(":")[2])
    node = await db.get_node(node_id)
    panel_rec = await db.get_master_server("v2ray")
    if not node or not panel_rec:
        await callback.answer("❌ سرور/پنل یافت نشد.", show_alert=True)
        return
    await callback.message.edit_text(f"⏳ تغییر سرور فعال به «{node['name']}» (آروان + سینک)...")
    res = await panel_sync.switch_active(panel_rec, node)
    await callback.message.edit_text(
        "🟢 سرور فعال تنظیم شد\n"
        f"سرور: {node['name']} ({node['ip']})\n"
        f"آروان (تغییر DNS): {'✅' if res['arvan'] else '❌'}\n"
        f"سینک کلاینت‌ها: {'✅' if res['sync'] else '❌'}\n"
        f"{res['error']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")],
        ]),
    )
    await callback.answer("✅ انجام شد")
