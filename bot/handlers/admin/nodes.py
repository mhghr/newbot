from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    nodes_list_keyboard, node_actions_keyboard, node_delete_confirm_keyboard, cancel_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)

FIELD_LABELS = {
    "name": "نام",
    "ip": "آی‌پی",
    "ssh_port": "پورت SSH",
    "ssh_user": "یوزر SSH",
    "ssh_pass": "پسورد SSH",
    "location": "لوکیشن",
}


class AddNodeStates(StatesGroup):
    waiting_name = State()
    waiting_ip = State()
    waiting_ssh_port = State()
    waiting_ssh_user = State()
    waiting_ssh_pass = State()
    waiting_location = State()


class NodeEditStates(StatesGroup):
    waiting_value = State()


def _node_header(node) -> str:
    status = "فعال 🟢" if node["is_active"] else "غیرفعال 🔴"
    kind = "ایران" if db.is_iran_location(node["location"]) else "خارج"
    return (
        f"🖥 {node['name']} ({kind}) — {status}\n"
        f"🌐 {node['ip']}:{node.get('ssh_port') or 22}\n"
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
            "📋 هیچ سروری ثبت نشده است.\n(ایران و خارج هر دو اینجا اضافه می‌شوند)",
            reply_markup=_empty_nodes_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🖥 مدیریت سرورها (ایران و خارج):\n\nروی هر سرور بزنید تا ویرایش/تنظیم کنید:",
        reply_markup=nodes_list_keyboard(nodes),
    )
    await callback.answer()


# ---------------- Add node ----------------

@router.callback_query(F.data == "admin:add_node")
async def add_node_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text("🖥 افزودن سرور\n\n📛 نام سرور را وارد کنید:", reply_markup=cancel_keyboard())
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
    await message.answer("🔢 پورت SSH را وارد کنید (پیش‌فرض 22):", reply_markup=cancel_keyboard())
    await state.set_state(AddNodeStates.waiting_ssh_port)


@router.message(AddNodeStates.waiting_ssh_port)
async def add_node_port(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    t = message.text.strip()
    await state.update_data(ssh_port=int(t) if t.isdigit() else 22)
    await message.answer("👤 یوزر SSH (پیش‌فرض root):", reply_markup=cancel_keyboard())
    await state.set_state(AddNodeStates.waiting_ssh_user)


@router.message(AddNodeStates.waiting_ssh_user)
async def add_node_user(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(ssh_user=message.text.strip() or "root")
    await message.answer("🔑 پسورد SSH:", reply_markup=cancel_keyboard())
    await state.set_state(AddNodeStates.waiting_ssh_pass)


@router.message(AddNodeStates.waiting_ssh_pass)
async def add_node_pass(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(ssh_pass=message.text.strip())
    await message.answer(
        "📍 لوکیشن را وارد کنید:\n(برای سرورهای ایران بنویسید: `iran`؛ برای خارج نام شهر/کشور)",
        parse_mode="Markdown", reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddNodeStates.waiting_location)


@router.message(AddNodeStates.waiting_location)
async def add_node_location(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(location=message.text.strip())
    data = await state.get_data()
    await db.add_node(
        name=data["name"], ip=data["ip"],
        ssh_port=data.get("ssh_port", 22), ssh_user=data.get("ssh_user", "root"),
        ssh_pass=data.get("ssh_pass", ""), location=data.get("location", ""),
    )
    await state.clear()
    nodes = await db.get_all_nodes()
    await message.answer(f"✅ سرور «{data['name']}» اضافه شد!", reply_markup=nodes_list_keyboard(nodes))


# ---------------- Detail / edit / toggle / delete ----------------

@router.callback_query(F.data.startswith("admin:node_detail:"))
async def node_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    node = await db.get_node(nid)
    if not node:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return
    await callback.message.edit_text(_node_header(node), reply_markup=node_actions_keyboard(node))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_node:"))
async def edit_node_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    nid = int(parts[2])
    field = parts[3]
    await state.update_data(node_id=nid, node_field=field)
    hint = "\n(عدد)" if field == "ssh_port" else ""
    await callback.message.edit_text(
        f"✏️ {FIELD_LABELS.get(field, field)}:\n\nمقدار جدید را وارد کنید:{hint}",
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
    value = message.text.strip()
    if field == "ssh_port" and not value.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید:", reply_markup=cancel_keyboard())
        return
    await db.update_node_field(nid, field, value)
    await state.clear()
    node = await db.get_node(nid)
    await message.answer("✅ ذخیره شد.\n\n" + _node_header(node), reply_markup=node_actions_keyboard(node))


@router.callback_query(F.data.startswith("admin:toggle_node:"))
async def toggle_node(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    nid = int(callback.data.split(":")[2])
    await db.toggle_node(nid)
    node = await db.get_node(nid)
    await callback.message.edit_text(_node_header(node), reply_markup=node_actions_keyboard(node))
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
