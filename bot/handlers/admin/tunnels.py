from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import random
import string
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services import tunnel as tunnel_svc
from bot.keyboards.inline import cancel_keyboard, admin_menu_keyboard
from bot.services.xui import XUIClient

router = Router()
logger = logging.getLogger(__name__)


class TunnelPanelStates(StatesGroup):
    waiting_url = State()
    waiting_token = State()


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
    asyncio.create_task(_do_provision(bot, callback.from_user.id, tunnel, dict(iran), dict(foreign), state))


async def _do_provision(bot: Bot, chat_id: int, tunnel: dict, iran: dict, foreign: dict,
                        state: FSMContext):
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

    # The tunnel is established. Ask the admin for the Iran node's x-ui panel
    # credentials; the location inbound + routing are created once they arrive.
    await state.update_data(tid=tid, iran_id=iran.get("id"))
    await state.set_state(TunnelPanelStates.waiting_url)
    await bot.send_message(
        chat_id,
        f"✅ تانل #{tid} برقرار شد.\n\n"
        "🌐 حالا آدرس پنل x-ui سرور ایران را وارد کنید:\n"
        "(مثال: https://1.2.3.4:9999/janan)",
        reply_markup=cancel_keyboard(),
    )


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
    mapping = await db.get_tunnel_inbound_on_node(t["iran_node_id"], tid) if t["iran_node_id"] else None
    host_line = ""
    if mapping:
        inbound_id = mapping["inbound_id"]
        host_line = f"🔌 اینباند: {inbound_id} ({mapping.get('tag') or '-'})\n"
        try:
            node = await db.get_node(t["iran_node_id"])
            purl, ptok = await tunnel_svc._find_node_panel(node["ip"])
            if purl:
                groups = await XUIClient(purl, api_token=ptok or "").get_hosts_by_inbound(inbound_id)
                if groups:
                    hosts = groups[0].get("hosts") or []
                    host_line += f"🌐 هاست: {', '.join(hosts) if hosts else '(خالی)'}\n"
                else:
                    host_line += "🌐 هاست: (ندارد)\n"
        except Exception:
            pass
    text = (
        f"🚇 تانل #{tid}\n"
        f"🇮🇷 ایران: {iran['name'] if iran else '-'}\n"
        f"🌍 خارج: {foreign['name'] if foreign else '-'}\n"
        f"وضعیت: {t['status']}\n"
        f"{host_line}"
        f"جزئیات: {(t.get('detail') or '-')[:600]}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 ساخت/سینک لوکیشن", callback_data=f"admin:tunnel_sync:{tid}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:tunnels")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:tunnel_sync:"))
async def tunnel_sync(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    tid = int(callback.data.split(":")[2])
    t = await db.get_tunnel(tid)
    if not t:
        await callback.answer("❌ تانل یافت نشد!", show_alert=True)
        return
    iran = await db.get_node(t["iran_node_id"]) if t["iran_node_id"] else None
    foreign = await db.get_node(t["foreign_node_id"]) if t["foreign_node_id"] else None
    if not iran or not foreign:
        await callback.answer("❌ سرورهای تانل یافت نشد!", show_alert=True)
        return

    panel_url = (iran.get("panel_url") or "").strip()
    panel_tok = iran.get("panel_token") or ""
    if not panel_url:
        panel_url, panel_tok = await tunnel_svc._find_node_panel(iran["ip"])
    if not panel_url:
        await callback.answer("⚠️ اطلاعات پنل این سرور تنظیم نشده.", show_alert=True)
        return

    await callback.answer("⏳ در حال ساخت لوکیشن...")
    status = await callback.message.answer("⏳ در حال ساخت اینباند/روتینگ لوکیشن...")
    try:
        tdict = {
            "id": tid,
            "subnet": f"10.240.{tid}.0/30",
            "foreign_tap_ip": f"10.240.{tid}.2",
        }
        loc = await tunnel_svc.setup_location(
            panel_url, panel_tok, dict(iran), dict(foreign), tdict
        )
        await db.update_tunnel(
            tid, "up", loc["note"][:400],
            inbound_id=loc.get("inbound_id"),
            location=loc.get("name") or loc.get("tag") or "",
        )
        try:
            await status.delete()
        except Exception:
            pass
        await callback.message.answer(
            f"✅ لوکیشن تانل #{tid} آماده شد.\n\n📍 {loc.get('name')}\n"
            f"🆔 inbound id: {loc.get('inbound_id')}\n🧩 {loc.get('note')}",
            reply_markup=admin_menu_keyboard(),
        )
    except Exception as e:
        logger.exception("tunnel location sync failed")
        try:
            await status.edit_text(f"❌ ساخت لوکیشن ناموفق بود:\n{str(e)[:250]}")
        except Exception:
            await callback.message.answer(f"❌ ساخت لوکیشن ناموفق بود:\n{str(e)[:250]}")
        await callback.message.answer("منوی مدیریت:", reply_markup=admin_menu_keyboard())


@router.message(TunnelPanelStates.waiting_url)
async def tunnel_panel_url(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    url = (message.text or "").strip()
    if not url:
        await message.answer("⚠️ آدرس پنل نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return
    url = await tunnel_svc.normalize_panel_url(url)
    await state.update_data(panel_url=url)
    await message.answer(
        "🔑 توکن API پنل را وارد کنید:\n"
        "(از پنل x-ui → Settings → API Token)",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(TunnelPanelStates.waiting_token)


@router.message(TunnelPanelStates.waiting_token)
async def tunnel_panel_token(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    token = (message.text or "").strip()
    data = await state.get_data()
    tid = data.get("tid")
    panel_url = data.get("panel_url")
    await state.clear()
    if not tid or not panel_url:
        await message.answer("❌ اطلاعات ناقص است؛ دوباره از «تانل‌ها» شروع کنید.",
                             reply_markup=admin_menu_keyboard())
        return

    tunnel = await db.get_tunnel(tid)
    if not tunnel:
        await message.answer("❌ تانل یافت نشد.", reply_markup=admin_menu_keyboard())
        return
    iran = await db.get_node(tunnel["iran_node_id"])
    foreign = await db.get_node(tunnel["foreign_node_id"])
    if not iran or not foreign:
        await message.answer("❌ سرورهای تانل یافت نشد.", reply_markup=admin_menu_keyboard())
        return

    status = await message.answer("⏳ در حال اتصال به پنل و ساخت اینباند/روتینگ...")
    try:
        # Persist the panel info on the node so later client creation uses it.
        await db.set_node_panel(iran["id"], panel_url, token)
        tunnel_dict = {
            "id": tid,
            "subnet": f"10.240.{tid}.0/30",
            "foreign_tap_ip": f"10.240.{tid}.2",
        }
        loc = await tunnel_svc.setup_location(panel_url, token, dict(iran), dict(foreign), tunnel_dict)
        await db.update_tunnel(
            tid, "up", loc["note"][:400],
            inbound_id=loc.get("inbound_id"),
            location=loc.get("name") or loc.get("tag") or "",
        )
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(
            f"✅ تانل #{tid} کامل شد.\n\n📍 لوکیشن: {loc.get('name')}\n"
            f"🆔 inbound id: {loc.get('inbound_id')}\n"
            f"🧩 {loc.get('note')}",
            reply_markup=admin_menu_keyboard(),
        )
    except Exception as e:
        logger.exception("location setup after panel info failed")
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(f"❌ ساخت لوکیشن ناموفق بود:\n{str(e)[:250]}",
                             reply_markup=admin_menu_keyboard())
