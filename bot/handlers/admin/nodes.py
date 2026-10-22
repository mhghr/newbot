from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services import tunnel as tunnel_svc
from bot.keyboards.inline import (
    admin_menu_keyboard, nodes_list_keyboard, node_actions_keyboard,
    node_delete_confirm_keyboard, cancel_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)

FIELD_LABELS = {
    "name": "نام",
    "ip": "آی‌پی",
    "panel_url": "آدرس پنل",
    "panel_token": "توکن پنل",
}


class AddNodeStates(StatesGroup):
    waiting_name = State()
    waiting_ip = State()
    waiting_panel_url = State()
    waiting_panel_token = State()


class NodeEditStates(StatesGroup):
    waiting_value = State()


def _node_header(node) -> str:
    status = "فعال 🟢" if node["is_active"] else "غیرفعال 🔴"
    return (
        f"🖥 {node['name']} — {status}\n"
        f"🌐 {node['ip']}\n"
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
    await message.answer("🌐 آی‌پی سرور را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddNodeStates.waiting_ip)


@router.message(AddNodeStates.waiting_ip)
async def add_node_ip(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(ip=message.text.strip())
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
    ip = (data.get("ip") or "").strip()
    if not name or not ip:
        await state.clear()
        await message.answer("⚠️ نام یا آی‌پی خالی است؛ دوباره تلاش کنید.",
                             reply_markup=admin_menu_keyboard())
        return

    await db.add_node(
        name=name, ip=ip, ssh_port=22, ssh_user="root", ssh_pass="",
        panel_url=(data.get("panel_url") or ""),
        panel_token=(data.get("panel_token") or ""),
    )
    await state.clear()
    nodes = await db.get_all_nodes()
    await message.answer(
        f"✅ سرور «{name}» اضافه شد.",
        reply_markup=nodes_list_keyboard(nodes),
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
        f"⚠️ حذف «{node['name']}» ({node['ip']})؟\nاین عمل قابل بازگشت نیست.",
        reply_markup=node_delete_confirm_keyboard(nid),
    )
    await callback.answer()
