from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from urllib.parse import urlparse
import json
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services import tunnel as tunnel_svc
from bot.services.xui import XUIClient
from bot.keyboards.inline import (
    admin_menu_keyboard, nodes_list_keyboard, node_actions_keyboard,
    node_delete_confirm_keyboard, node_inbounds_keyboard, cancel_keyboard,
)
from bot.handlers.admin.servers import _sync_clients_to_inbounds

router = Router()
logger = logging.getLogger(__name__)

FIELD_LABELS = {
    "name": "نام",
    "panel_url": "آدرس پنل",
    "panel_token": "توکن پنل",
}


class AddNodeStates(StatesGroup):
    waiting_name = State()
    waiting_panel_url = State()
    waiting_panel_token = State()


class NodeEditStates(StatesGroup):
    waiting_value = State()


def _node_header(node) -> str:
    status = "فعال 🟢" if node["is_active"] else "غیرفعال 🔴"
    return (
        f"🖥 {node['name']} — {status}\n"
        f"🔗 {node.get('panel_url') or '-'}\n"
        "روی هر مورد بزنید تا ویرایش شود:"
    )


def _empty_nodes_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن سرور", callback_data="admin:add_node")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
    ])


@router.callback_query(F.data == "admin:nodes")
async def nodes_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    nodes = await db.get_all_nodes()
    if not nodes:
        await callback.message.edit_text(
            "📋 هیچ سروری ثبت نشده است.\n(فقط اطلاعات پنل ثبت می‌شود)",
            reply_markup=_empty_nodes_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🖥 مدیریت سرورها:\n\nروی هر سرور بزنید تا ویرایش/تنظیم کنید:",
        reply_markup=nodes_list_keyboard(nodes),
    )
    await callback.answer()


# ---------------- Add node ----------------

@router.callback_query(F.data == "admin:add_node")
async def add_node_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text("📛 نام سرور را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddNodeStates.waiting_name)
    await callback.answer()


@router.message(AddNodeStates.waiting_name)
async def add_node_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(name=message.text.strip())
    await message.answer(
        "🌐 آدرس پنل x-ui را وارد کنید:\n(مثال: https://irancell.manointernet.ir:9999/janan)",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddNodeStates.waiting_panel_url)


@router.message(AddNodeStates.waiting_panel_url)
async def add_node_panel_url(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    url = (message.text or "").strip()
    if not url:
        await message.answer("⚠️ آدرس نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return
    await state.update_data(panel_url=await tunnel_svc.normalize_panel_url(url))
    await message.answer("🔑 توکن API پنل:", reply_markup=cancel_keyboard())
    await state.set_state(AddNodeStates.waiting_panel_token)


@router.message(AddNodeStates.waiting_panel_token)
async def add_node_panel_token(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(panel_token=(message.text or "").strip())
    await _finish_add_node(message, state)


async def _finish_add_node(message: Message, state: FSMContext):
    data = await state.get_data()
    name = (data.get("name") or "").strip()
    panel_url = (data.get("panel_url") or "").strip()
    if not name:
        await state.clear()
        await message.answer("⚠️ نام سرور نامعتبر است. دوباره از منو تلاش کنید.",
                             reply_markup=admin_menu_keyboard())
        return

    # Do not save a server we cannot reach: verify the panel URL + token first.
    panel_token = (data.get("panel_token") or "").strip()
    xui = XUIClient(panel_url, api_token=panel_token)
    ok, err = await xui.check_connection()
    if not ok:
        await message.answer(
            f"❌ اتصال به پنل برقرار نشد؛ سرور ذخیره نشد.\n{err}\n\n"
            "دوباره توکن API را وارد کنید یا انصراف بزنید:",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(AddNodeStates.waiting_panel_token)
        return

    # The IP is no longer asked for; derive the host from the panel address.
    try:
        ip = urlparse(panel_url).hostname or ""
    except Exception:
        ip = ""

    await db.add_node(
        name=name, ip=ip, ssh_port=22, ssh_user="root", ssh_pass="",
        panel_url=panel_url,
        panel_token=panel_token,
    )
    await state.clear()
    nodes = await db.get_all_nodes()
    await message.answer(
        f"✅ سرور «{name}» اضافه شد.",
        reply_markup=nodes_list_keyboard(nodes),
    )


# ---------------- Node inbounds ----------------

class NodeInboundStates(StatesGroup):
    waiting_add = State()


def _node_inbounds_screen(node):
    ids = db.parse_inbound_ids(node.get("inbound_ids") or "")
    text = (
        f"📋 لیست اینباندهای «{node['name']}»\n\n"
        f"ثبت‌شده: {', '.join(str(i) for i in ids) if ids else 'خالی'}\n\n"
        "برای حذف روی شماره اینباند بزنید،\n"
        "یا با «➕ افزودن اینباند» شماره جدید اضافه کنید:"
    )
    return text, node_inbounds_keyboard(node["id"], ids)


@router.callback_query(F.data.startswith("admin:node_inbounds:"))
async def node_inbounds_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    node = await db.get_node(nid)
    if not node:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return
    text, kb = _node_inbounds_screen(node)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:node_inbound_add:"))
async def node_inbound_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    await state.update_data(node_inbound_id=nid)
    await callback.message.edit_text(
        "➕ شماره اینباند مورد نظر را وارد کنید (مثال: 3):",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(NodeInboundStates.waiting_add)
    await callback.answer()


@router.message(NodeInboundStates.waiting_add)
async def node_inbound_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    nid = data["node_inbound_id"]
    ids = db.parse_inbound_ids(message.text)
    if not ids:
        await message.answer("⚠️ ورودی نامعتبر.", reply_markup=cancel_keyboard())
        return
    for iid in ids:
        await db.add_node_inbound_id(nid, iid)
    await state.clear()
    node = await db.get_node(nid)
    text, kb = _node_inbounds_screen(node)
    await message.answer("✅ اضافه شد.\n\n" + text, reply_markup=kb)


@router.callback_query(F.data.startswith("admin:node_inbound_del:"))
async def node_inbound_del_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    nid = int(parts[2]); iid = int(parts[3])
    node = await db.get_node(nid)
    if not node:
        await callback.answer("❌", show_alert=True); return
    await callback.message.edit_text(
        f"🗑 حذف اینباند {iid} از «{node['name']}»؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ بله، حذف",
                callback_data=f"admin:node_inbound_del_ok:{nid}:{iid}",
            )],
            [InlineKeyboardButton(
                text="🔙 بازگشت",
                callback_data=f"admin:node_inbounds:{nid}",
            )],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:node_inbound_del_ok:"))
async def node_inbound_del(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    nid = int(parts[2]); iid = int(parts[3])
    await db.remove_node_inbound_id(nid, iid)
    node = await db.get_node(nid)
    text, kb = _node_inbounds_screen(node)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer(f"✅ اینباند {iid} حذف شد", show_alert=True)


@router.callback_query(F.data.startswith("admin:node_sync_inbounds:"))
async def node_sync_inbounds(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    node = await db.get_node(nid)
    if not node:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True); return

    panel_url = (node.get("panel_url") or "").strip()
    panel_token = (node.get("panel_token") or "").strip()
    if not panel_url:
        await callback.answer("❌ آدرس پنل برای این سرور ثبت نشده.", show_alert=True)
        return

    await callback.message.edit_text("⏳ در حال دریافت اینباندها از پنل...")
    xui = XUIClient(panel_url, api_token=panel_token)
    ok, err = await xui.check_connection()
    if not ok:
        await callback.message.edit_text(
            f"❌ اتصال به پنل ناموفق بود.\n{err}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admin:node_detail:{nid}")],
            ]),
        )
        await callback.answer()
        return

    inbounds = await xui.get_inbounds()
    ids = db.parse_inbound_ids(node.get("inbound_ids") or "")
    if not ids:
        ids = [
            int(ib["id"]) for ib in inbounds
            if ib.get("id") is not None
            and ib.get("enable", True)
            and (ib.get("protocol") or "") == "vless"
        ]
        if ids:
            await db.set_node_inbound_ids(nid, ",".join(str(i) for i in ids))

    if not ids:
        await callback.message.edit_text(
            "❌ هیچ اینباندی برای سینک پیدا نشد.\n"
            "ابتدا از «📋 لیست اینباندها» اینباند اضافه کنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"admin:node_detail:{nid}")],
            ]),
        )
        await callback.answer()
        return

    # Every client that lives on this node's panel.
    emails = []
    for ib in inbounds:
        for cs in (ib.get("clientStats") or []):
            em = (cs.get("email") or "").strip()
            if em and em not in emails:
                emails.append(em)
        settings = ib.get("settings")
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        if isinstance(settings, dict):
            for c in (settings.get("clients") or []):
                em = (c.get("email") or "").strip()
                if em and em not in emails:
                    emails.append(em)

    panel = {
        "id": nid,
        "name": node["name"],
        "url": panel_url,
        "username": "",
        "password": "",
        "api_token": panel_token,
        "inbound_ids": ",".join(str(i) for i in ids),
    }
    await callback.answer("🔄 سینک شروع شد")
    await callback.message.edit_text(
        f"🔄 سینک اینباندها: {', '.join(str(i) for i in ids)}\n"
        "⏳ در حال اتچ کردن کلاینت‌ها..."
    )
    await _sync_clients_to_inbounds(
        panel, callback.from_user.id, bot,
        status_message=callback.message, emails=emails,
    )


# ---------------- Detail / edit / toggle / delete ----------------

async def _render_node(node, **kwargs):
    return _node_header(node), node_actions_keyboard(node)


@router.callback_query(F.data.startswith("admin:node_detail:"))
async def node_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    node = await db.get_node(nid)
    if not node:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return
    text, kb = await _render_node(node)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_node:"))
async def edit_node_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    nid = int(parts[2])
    field = parts[3]
    await state.update_data(node_id=nid, node_field=field)
    await callback.message.edit_text(
        f"✏️ {FIELD_LABELS.get(field, field)}:\n\nمقدار جدید را وارد کنید:",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(NodeEditStates.waiting_value)
    await callback.answer()


@router.message(NodeEditStates.waiting_value)
async def edit_node_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    nid = data["node_id"]
    field = data["node_field"]
    value = (message.text or "").strip()
    await db.update_node_field(nid, field, value)

    await state.clear()
    node = await db.get_node(nid)
    text, kb = await _render_node(node)
    await message.answer("✅ ذخیره شد.\n\n" + text, reply_markup=kb)


@router.callback_query(F.data.startswith("admin:toggle_node:"))
async def toggle_node(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    await db.toggle_node(nid)
    node = await db.get_node(nid)
    text, kb = await _render_node(node)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("✅ وضعیت تغییر کرد!")


@router.callback_query(F.data.startswith("admin:delete_node_confirm:"))
async def delete_node(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    await db.delete_node(nid)
    await callback.answer("✅ حذف شد!")
    nodes = await db.get_all_nodes()
    if nodes:
        await callback.message.edit_text("🖥 مدیریت سرورها:", reply_markup=nodes_list_keyboard(nodes))
    else:
        await callback.message.edit_text("📋 هیچ سروری ثبت نشده است.", reply_markup=_empty_nodes_kb())


@router.callback_query(F.data.startswith("admin:delete_node:"))
async def delete_node_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    node = await db.get_node(nid)
    if not node:
        await callback.answer("❌", show_alert=True)
        return
    await callback.message.edit_text(
        f"⚠️ حذف «{node['name']}»؟\nاین عمل قابل بازگشت نیست.",
        reply_markup=node_delete_confirm_keyboard(nid),
    )
    await callback.answer()
