from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    servers_list_keyboard, server_actions_keyboard, server_inbounds_keyboard,
    server_delete_confirm_keyboard, cancel_keyboard,
)
from bot.services.xui import XUIClient

router = Router()
logger = logging.getLogger(__name__)


def _rec_get(rec, key, default=None):
    try:
        value = rec[key]
        return default if value is None else value
    except (KeyError, IndexError):
        return default


async def _sync_clients_to_inbounds(server: dict, admin_chat_id: int, bot,
                                    status_message=None, emails=None):
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

        if emails is None:
            configs = await db.get_configs_by_server_id(server["id"])
            if not configs:
                msg = "هیچ کانفیگ فعالی برای این سرور یافت نشد."
                if status_message:
                    await status_message.edit_text(msg)
                else:
                    await bot.send_message(admin_chat_id, msg)
                return

            # An account may have several device clients; sync every one.
            emails = []
            for cfg in configs:
                for em in (_rec_get(cfg, "client_emails", "") or "").split(","):
                    em = em.strip()
                    if em and em not in emails:
                        emails.append(em)
                single = (_rec_get(cfg, "client_email", "") or "").strip()
                if single and single not in emails:
                    emails.append(single)
        else:
            emails = list(dict.fromkeys(e for e in emails if e))

        if not emails:
            msg = "هیچ کلاینتی برای این سرور یافت نشد."
            if status_message:
                await status_message.edit_text(msg)
            else:
                await bot.send_message(admin_chat_id, msg)
            return

        if status_message:
            await status_message.edit_text(
                f"⏳ سینک {len(emails)} کلاینت روی اینباندهای "
                f"{', '.join(str(i) for i in desired_ids)}..."
            )

        fixed = 0
        already_ok = 0
        failed = 0
        desired_set = set(desired_ids)

        for email in emails:
            try:
                current_set = set(await xui.get_client_inbounds(email))
                if current_set == desired_set:
                    already_ok += 1
                    continue

                # Remove the old attachments first, then attach the new
                # inbounds one by one, and verify the final state.
                to_remove = sorted(current_set - desired_set)
                if to_remove:
                    await xui.detach_client(email, to_remove)
                for iid in desired_ids:
                    await xui.attach_client(email, [iid])

                actual = set(await xui.get_client_inbounds(email))
                if actual == desired_set:
                    fixed += 1
                else:
                    failed += 1
                    logger.error(
                        "Inbound sync verification failed for %s: desired=%s actual=%s",
                        email, sorted(desired_set), sorted(actual),
                    )
            except Exception:
                failed += 1
                logger.exception(f"Sync failed for client {email}")

        msg = (
            f"گزارش سینک اینباندهای «{server['name']}»\n\n"
            f"اینباندهای هدف: {', '.join(str(i) for i in desired_ids)}\n"
            f"از قبل درست: {already_ok}\n"
            f"سینک شد: {fixed}\n"
            f"خطا: {failed}\n"
            f"مجموع کلاینت‌ها: {len(emails)}"
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

async def _panel_inbounds(server) -> list:
    """Fetch the live inbound list from the server's 3x-ui panel."""
    xui = XUIClient(
        server["url"], server.get("username") or "",
        server.get("password") or "", server.get("api_token") or "",
    )
    try:
        return await xui.get_inbounds()
    except Exception as e:
        logger.warning("Panel inbound list failed for %s: %s", server.get("url"), e)
        return []


FIELD_LABELS = {
    "name": "نام", "url": "آدرس", "api_token": "توکن",
    "sub_domain": "آدرس ساب", "sub_port": "پورت ساب",
    "username": "یوزر SSH/API", "password": "پسورد SSH/API", "api_port": "پورت SSH/API",
}


class AddServerStates(StatesGroup):
    # V2Ray (3x-ui)
    waiting_url = State()
    waiting_token = State()
    waiting_name = State()
    waiting_inbounds = State()


class InboundStates(StatesGroup):
    waiting_inbound_ids = State()
    waiting_add_inbound = State()


class ServerEditStates(StatesGroup):
    waiting_value = State()


def _server_header(server, role: str = "") -> str:
    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    lines = [
        f"🖥 {server['name']}  ({status})",
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
            "📋 هیچ پنلی ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
            ])
        )
        await callback.answer()
        return
    panel = servers[0]
    await callback.message.edit_text(
        "🗄 مدیریت پنل\n\n" + _server_header(panel),
        reply_markup=server_actions_keyboard(panel),
    )
    await callback.answer()


# ---------------- Add server flow ----------------

@router.callback_query(F.data == "admin:add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "🖥 افزودن سرور\n\nآدرس پنل 3x-ui را وارد کنید:\n(مثال: https://panel.example.com:2053/path)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_url)
    await callback.answer()


@router.callback_query(F.data == "new_server_type:v2ray")
async def add_server_v2ray_compat(callback: CallbackQuery, state: FSMContext):
    """Compatibility for keyboards sent before service types were simplified."""
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "🖥 افزودن سرور\n\nآدرس پنل 3x-ui را وارد کنید:\n(مثال: https://panel.example.com:2053/path)",
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
    data = await state.get_data()
    await message.answer("⏳ در حال تست اتصال به پنل...")
    xui = XUIClient(data["url"], api_token=data["api_token"])
    ok, err = await xui.check_connection()
    if not ok:
        await message.answer(
            f"❌ اتصال به پنل ناموفق بود؛ سرور ذخیره نشد.\n{err}\n\n"
            "دوباره توکن API را وارد کنید یا انصراف بزنید:",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(AddServerStates.waiting_token)
        return

    inbounds = await xui.get_inbounds()
    inbound_id = inbounds[0].get("id", 0) if inbounds else 0
    await state.update_data(inbound_id=inbound_id)
    await message.answer(
        "✅ اتصال برقرار شد!\n\n📡 لیست آیدی اینباندها را وارد کنید:\n"
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
        name=data["name"], url=data["url"],
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
        f"📋 لیست اینباندهای «{server['name']}»\n\n"
        f"ثبت‌شده: {', '.join(str(i) for i in ids) if ids else 'خالی'}\n\n"
        "برای حذف روی شماره اینباند بزنید،\n"
        "یا با «➕ افزودن اینباند» شماره جدید اضافه کنید:",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:sync_inbounds:"))
async def sync_inbounds(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True); return

    # Attach the ids the admin has chosen. Only when none are saved yet, fall
    # back to the panel's enabled VLESS inbounds.
    cleaned = ",".join(str(i) for i in db.parse_inbound_ids(server.get("inbound_ids")))
    if not cleaned:
        await callback.message.edit_text("⏳ در حال دریافت اینباندها از پنل...")
        inbounds = await _panel_inbounds(server)
        ids = [
            int(ib["id"]) for ib in inbounds
            if ib.get("id") is not None
            and ib.get("enable", True)
            and (ib.get("protocol") or "") == "vless"
        ]
        if ids:
            cleaned = ",".join(str(i) for i in ids)
            await db.set_server_inbound_ids(server_id, cleaned)
            server = await db.get_server(server_id)

    if not cleaned:
        await callback.message.edit_text(
            "❌ هیچ اینباندی برای سینک پیدا نشد (نه روی پنل و نه ذخیره‌شده).\n"
            "ابتدا از «📋 لیست اینباندها» اینباند اضافه کنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data=f"admin:server_detail:{server_id}",
                )],
            ]),
        )
        await callback.answer()
        return

    await callback.answer("🔄 سینک شروع شد")
    await callback.message.edit_text(
        f"🔄 سینک اینباندها: {cleaned}\n⏳ در حال اتچ کردن کلاینت‌ها..."
    )
    await _sync_clients_to_inbounds(
        server, callback.from_user.id, bot, status_message=callback.message
    )


@router.callback_query(F.data.startswith("admin:inbound_del:"))
async def inbound_delete_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    parts = callback.data.split(":")
    server_id = int(parts[2]); inbound_id = int(parts[3])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌", show_alert=True); return
    await callback.message.edit_text(
        f"🗑 حذف اینباند {inbound_id} از «{server['name']}»؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ بله، حذف",
                callback_data=f"admin:inbound_del_ok:{server_id}:{inbound_id}",
            )],
            [InlineKeyboardButton(
                text="🔙 بازگشت",
                callback_data=f"admin:inbounds:{server_id}",
            )],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:inbound_del_ok:"))
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
    await callback.answer(f"✅ {inbound_id} حذف شد!", show_alert=True)


@router.callback_query(F.data.startswith("admin:inbound_add:"))
async def inbound_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    server_id = int(callback.data.split(":")[2])
    await state.update_data(inbounds_server_id=server_id)
    await callback.message.edit_text(
        "➕ شماره اینباند مورد نظر را وارد کنید (مثال: 10):",
        reply_markup=cancel_keyboard(),
    )
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
        f"⚠️ حذف «{server['name']}»؟\nاین عمل قابل بازگشت نیست.",
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
