from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
import asyncio
import random
import string
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services import tunnel as tunnel_svc

router = Router()
logger = logging.getLogger(__name__)


def _nodes_kb(nodes, prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{n['name']} — {n['ip']}", callback_data=f"{prefix}:{n['id']}")]
        for n in nodes
    ]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:tunnels")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admin:tunnels")
async def tunnels_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    tunnels = await db.get_all_tunnels()
    rows = []
    for t in tunnels:
        iran = await db.get_node(t["iran_node_id"]) if t["iran_node_id"] else None
        foreign = await db.get_node(t["foreign_node_id"]) if t["foreign_node_id"] else None
        iname = iran["name"] if iran else "?"
        fname = foreign["name"] if foreign else "?"
        rows.append([InlineKeyboardButton(
            text=f"#{t['id']} {iname} ↔ {fname} [{t['status']}]",
            callback_data=f"admin:tunnel_detail:{t['id']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ افزودن تانل", callback_data="admin:add_tunnel")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
    await callback.message.edit_text("🚇 تانل‌ها:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "admin:add_tunnel")
async def add_tunnel_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    iran = await db.get_iran_nodes()
    if not iran:
        await callback.answer(
            "❌ هیچ سرور ایرانی ثبت نشده. اول در «مدیریت سرورها» سرور با لوکیشن iran اضافه کنید.",
            show_alert=True,
        )
        return
    await callback.message.edit_text(
        "🚇 افزودن تانل\n\n1️⃣ سرور ایران را انتخاب کنید:", reply_markup=_nodes_kb(iran, "tunnel_iran")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tunnel_iran:"))
async def pick_iran(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    iran_id = int(callback.data.split(":")[1])
    await state.update_data(iran_id=iran_id)
    foreign = await db.get_foreign_nodes()
    if not foreign:
        await callback.answer("❌ هیچ سرور خارجی ثبت نشده.", show_alert=True)
        return
    await callback.message.edit_text(
        "2️⃣ سرور خارج را انتخاب کنید:", reply_markup=_nodes_kb(foreign, "tunnel_foreign")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tunnel_foreign:"))
async def pick_foreign(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        return
    foreign_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    iran_id = data.get("iran_id")
    await state.clear()
    iran = await db.get_node(iran_id)
    foreign = await db.get_node(foreign_id)
    if not iran or not foreign:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    tid = await db.add_tunnel(iran_id, foreign_id)
    tunnel = {
        "id": tid,
        "userpass": "".join(random.choices(string.ascii_letters + string.digits, k=12)),
        "subnet": f"10.240.{tid}.0/30",
        "iran_tap_ip": f"10.240.{tid}.1",
        "foreign_tap_ip": f"10.240.{tid}.2",
    }
    await callback.message.edit_text(
        f"⏳ در حال تست SSH و برقراری تانل SoftEther (#{tid})...\nممکن است چند دقیقه طول بکشد."
    )
    await callback.answer()
    asyncio.create_task(_do_provision(bot, callback.from_user.id, tunnel, dict(iran), dict(foreign)))


async def _do_provision(bot: Bot, chat_id: int, tunnel: dict, iran: dict, foreign: dict):
    tid = tunnel["id"]

    for label, node in (("ایران", iran), ("خارج", foreign)):
        try:
            await asyncio.to_thread(tunnel_svc.test_ssh, node)
        except Exception as e:
            await db.delete_tunnel(tid)
            await bot.send_message(
                chat_id,
                f"❌ اتصال SSH به سرور {label} «{node.get('name')}» "
                f"({node.get('ip')}:{node.get('ssh_port') or 22}) برقرار نشد.\n{e}",
            )
            return

    # Pre-check: report the current state and avoid duplicating an existing tunnel.
    try:
        check = await asyncio.to_thread(tunnel_svc.check_existing, iran, foreign)
    except Exception as e:
        check = f"check error: {e}"
    await bot.send_message(chat_id, f"🔍 بررسی وضعیت موجود (#{tid}):\n{check}")
    ip = iran.get("ip") or ""
    already_up = bool(ip and ip in check and "Established" in check)
    if already_up:
        await bot.send_message(
            chat_id,
            f"ℹ️ تانل {ip} از قبل «Established» است؛ SoftEther دست‌نخورده می‌ماند و فقط لوکیشن (اینباند) سینک می‌شود.",
        )
    else:
        try:
            await bot.send_message(chat_id, f"🔧 نصب/تنظیم SoftEther روی سرور ایران (#{tid})...")
            await asyncio.to_thread(tunnel_svc.provision_iran, iran, tunnel)
            await bot.send_message(chat_id, f"🔧 نصب/تنظیم SoftEther روی سرور خارج (#{tid})...")
            await asyncio.to_thread(tunnel_svc.provision_foreign, foreign, tunnel, iran["ip"])
            out = await asyncio.to_thread(tunnel_svc.test_tunnel, foreign, tunnel)
        except Exception as e:
            logger.exception("tunnel provision failed")
            await db.delete_tunnel(tid)
            await bot.send_message(chat_id, f"❌ خطا در برقراری تانل #{tid}:\n{str(e)[:300]}")
            return

    # Always (idempotent) create/sync the location inbound + routing on the Iran
    # panel, so get_locations() can list it (tunnel up AND inbound synced).
    loc_note = ""
    try:
        node_url, node_tok = await tunnel_svc._find_node_panel(iran["ip"])
    except Exception:
        node_url, node_tok = None, None
    if node_url:
        try:
            loc = await tunnel_svc.setup_location(node_url, node_tok, iran, foreign, tunnel)
            loc_note = f"\nراه‌اندازی لوکیشن:\n{loc['note']}"
            await db.update_tunnel(
                tid, "up", loc["note"][:400],
                inbound_id=loc.get("inbound_id"),
                location=loc.get("name") or loc.get("tag") or "",
            )
        except Exception as e:
            loc_note = f"\nراه‌اندازی لوکیشن ناموفق: {str(e)[:200]}"
    else:
        loc_note = "\nراه‌اندازی لوکیشن: پنلِ این نود پیدا نشد (توکن درست یافت نشد)."

    await bot.send_message(chat_id, f"✅ تانل #{tid} آماده است.{loc_note}")


@router.callback_query(F.data.startswith("admin:tunnel_detail:"))
async def tunnel_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    tid = int(callback.data.split(":")[2])
    t = await db.get_tunnel(tid)
    if not t:
        await callback.answer("❌", show_alert=True)
        return
    iran = await db.get_node(t["iran_node_id"]) if t["iran_node_id"] else None
    foreign = await db.get_node(t["foreign_node_id"]) if t["foreign_node_id"] else None
    text = (
        f"🚇 تانل #{tid}\n"
        f"🇮🇷 ایران: {iran['name'] if iran else '-'}\n"
        f"🌍 خارج: {foreign['name'] if foreign else '-'}\n"
        f"وضعیت: {t['status']}\n"
        f"جزئیات: {(t.get('detail') or '-')[:600]}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:tunnels")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()
